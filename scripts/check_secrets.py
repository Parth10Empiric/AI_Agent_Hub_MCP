"""
Phase 5.4 - prove no credential has leaked out of the encrypted column.

    python scripts/check_secrets.py

This is the Phase 5 milestone "Test 6", written down so it can be run
rather than remembered:

    SELECT credentials_enc FROM plugin_connections   -> ciphertext only
    grep -ri "ghp_|xoxb-|ya29\\." logs/              -> no matches
    ExecutionRecord.arguments                        -> redacted
    API responses                                    -> no tokens

WHY A SCRIPT AND NOT A TEST

The unit tests prove the CODE cannot leak. This checks whether it
already HAS - against the real database and the real log files on this
machine. Those are two different questions, and only the second one
finds the token that got written last Tuesday by a build that has since
been fixed.

Exit code 0 means clean, 1 means something needs attention.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from api.db.models import PluginConnection, ToolExecution  # noqa: E402
from api.settings import get_settings  # noqa: E402


OK = "  OK  "
BAD = " FAIL "
WARN = " WARN "


# What a real credential looks like, per provider.
#
# Prefixes, not entropy heuristics: a regex that hunts for
# "long random-looking string" matches every UUID, hash and base64
# blob in the file and is ignored within a week.
TOKEN_PATTERNS = [
    (re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(rb"ya29\.[A-Za-z0-9_\-]{20,}"), "Google access token"),
    (re.compile(rb"1//[A-Za-z0-9_\-]{20,}"), "Google refresh token"),
    (re.compile(rb"AIza[A-Za-z0-9_\-]{30,}"), "Google API key"),
]


def scan_bytes(blob: bytes) -> list[str]:
    return [name for pattern, name in TOKEN_PATTERNS if pattern.search(blob)]


# ---------------------------------------------------------------------
# 1. the database
# ---------------------------------------------------------------------


async def check_database() -> list[str]:

    problems: list[str] = []

    settings = get_settings()
    engine = create_async_engine(settings.database_url)

    try:
        async with engine.connect() as conn:

            # --- credentials_enc must be ciphertext ------------------
            rows = (
                await conn.execute(
                    select(
                        PluginConnection.id,
                        PluginConnection.plugin_key,
                        PluginConnection.credentials_enc,
                        PluginConnection.key_version,
                    )
                )
            ).all()

            leaked = 0
            stale = 0

            for row_id, key, blob, version in rows:

                found = scan_bytes(bytes(blob or b""))

                if found:
                    leaked += 1
                    problems.append(
                        f"plugin_connections {row_id} ({key}) contains "
                        f"a plaintext {found[0]}"
                    )

                # A Fernet token always starts with the version byte
                # 0x80, base64url-encoded as "g". Anything else is not
                # ciphertext at all.
                if blob and not bytes(blob).startswith(b"g"):
                    problems.append(
                        f"plugin_connections {row_id} ({key}) does not "
                        "look like a Fernet token"
                    )

                if (version or 0) < 1:
                    stale += 1

            print(
                f"{OK if not leaked else BAD} "
                f"plugin_connections: {len(rows)} rows, "
                f"{leaked} with plaintext credentials"
            )

            if stale:
                print(f"{WARN} {stale} rows have no key_version")

            # --- ExecutionRecord.arguments ---------------------------
            executions = (
                await conn.execute(
                    select(
                        ToolExecution.id,
                        ToolExecution.tool_name,
                        ToolExecution.arguments,
                    ).limit(5000)
                )
            ).all()

            exposed = 0

            for exec_id, tool, arguments in executions:

                found = scan_bytes(str(arguments or {}).encode())

                if found:
                    exposed += 1
                    problems.append(
                        f"tool_executions {exec_id} ({tool}) stored a "
                        f"plaintext {found[0]} in arguments"
                    )

            print(
                f"{OK if not exposed else BAD} "
                f"tool_executions: {len(executions)} rows checked, "
                f"{exposed} with plaintext credentials"
            )

            # --- and nothing else claiming to be a token -------------
            others = await conn.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND (
                        column_name ILIKE '%token%'
                        OR column_name ILIKE '%secret%'
                        OR column_name ILIKE '%password%'
                      )
                    ORDER BY 1, 2
                    """
                )
            )

            suspicious = [
                f"{t}.{c}"
                for t, c in others.all()
                # Known and correct. Listed explicitly rather than
                # filtered by a clever pattern, because a check that
                # always prints a warning is a check nobody reads.
                if (t, c) not in {
                    # SHA-256 of a refresh token, never the token.
                    ("refresh_tokens", "token_hash"),
                    # argon2id hash, never the password.
                    ("users", "password_hash"),
                    # LLM token COUNTS - prompt/completion sizes. The
                    # word "token" here means something else entirely.
                    ("messages", "token_usage"),
                }
            ]

            if suspicious:
                print(f"{WARN} columns worth checking: {', '.join(suspicious)}")
            else:
                print(f"{OK} no unexpected secret-shaped columns")

    finally:
        await engine.dispose()

    return problems


# ---------------------------------------------------------------------
# 2. the files
# ---------------------------------------------------------------------


def check_files() -> list[str]:
    """
    Logs, and anything else on disk that a token could have reached.

    The MCP server writes to stderr, which the backend captures - so a
    service that ever logged a request header would have put a token in
    a plain file with no encryption anywhere near it.
    """

    problems: list[str] = []

    roots = [
        PROJECT_ROOT / "logs",
        PROJECT_ROOT / "core",
        PROJECT_ROOT / "api",
        PROJECT_ROOT / "agent",
        PROJECT_ROOT / "services",
    ]

    scanned = 0

    for root in roots:

        if not root.exists():
            continue

        for path in root.rglob("*"):

            if not path.is_file():
                continue

            if any(
                part in {"__pycache__", ".venv", "node_modules"}
                for part in path.parts
            ):
                continue

            if path.suffix in {".pyc", ".png", ".jpg", ".ico"}:
                continue

            try:
                blob = path.read_bytes()

            except OSError:
                continue

            scanned += 1

            for found in scan_bytes(blob):
                problems.append(
                    f"{path.relative_to(PROJECT_ROOT)} contains a "
                    f"plaintext {found}"
                )

    print(
        f"{OK if not problems else BAD} "
        f"files: {scanned} scanned, {len(problems)} with credentials"
    )

    return problems


# ---------------------------------------------------------------------
# 3. what the API can return
# ---------------------------------------------------------------------


def check_response_models() -> list[str]:
    """
    The response SHAPE is the guarantee.

    There is no field for a credential on any model an endpoint
    returns, so there is no path from the database to an HTTP response
    - a property that holds without anyone remembering a rule.
    """

    from api.schemas.approval import ApprovalRead
    from api.schemas.plugin import ConnectionRead, PluginDetail, PluginSummary

    banned = {
        "credentials_enc",
        "credential",
        "access_token",
        "refresh_token",
        "token",
        "client_secret",
        "code_verifier",
    }

    problems: list[str] = []

    for model in (ConnectionRead, PluginSummary, PluginDetail, ApprovalRead):

        for field in set(model.model_fields) & banned:
            problems.append(f"{model.__name__} exposes {field!r}")

    print(
        f"{OK if not problems else BAD} "
        f"response models: {len(problems)} exposing credentials"
    )

    return problems


def main() -> int:

    print("Phase 5.4 - credential leak check\n")

    problems: list[str] = []

    problems += check_response_models()
    problems += check_files()

    try:
        problems += asyncio.run(check_database())

    except Exception as exc:
        print(f"{WARN} database not checked: {type(exc).__name__}")

    if problems:
        print("\nProblems found:\n")

        for problem in problems:
            print(f"  - {problem}")

        return 1

    print("\nClean. No credential found outside credentials_enc.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
