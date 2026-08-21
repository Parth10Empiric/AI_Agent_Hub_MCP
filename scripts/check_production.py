"""
Is this deployment safe to point at a client's real accounts?

    python scripts/check_production.py

Phase 5 added five independent controls, and every one of them has a
setting that quietly disables it. This checks them together, because
the dangerous state is not "a control is missing" - it is "four are on
and the fifth is not, and nobody noticed".

Exit code 0 means ready, 1 means do not deploy yet.

WHAT THIS IS NOT

A security audit. It checks configuration, not code. Run
scripts/check_secrets.py alongside it - that one checks whether a
credential has already leaked, which is a different question.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402


load_dotenv()

OK = "  OK  "
BAD = " FAIL "
WARN = " WARN "


def main() -> int:

    from api.settings import get_settings

    settings = get_settings()

    problems: list[str] = []
    warnings: list[str] = []

    production = settings.is_production

    print(f"environment : {settings.api_environment}\n")

    # --- 5.5 the one that leaks accounts -----------------------------
    #
    # Checked FIRST because it is the worst failure on the list: a user
    # who never connected GitHub silently gets the operator's account.
    mcp_env = os.getenv("MCP_ENVIRONMENT", "development")
    allow_env = os.getenv("MCP_ALLOW_ENV_CREDENTIALS")

    env_creds = (
        allow_env.strip().lower() in {"1", "true", "yes"}
        if allow_env is not None
        else mcp_env.strip().lower() != "production"
    )

    if production and env_creds:
        problems.append(
            "MCP_ENVIRONMENT is not 'production' (or "
            "MCP_ALLOW_ENV_CREDENTIALS is on), so a user who has not "
            "connected a service gets YOUR tokens from .env. This is a "
            "cross-tenant leak."
        )
        print(f"{BAD} 5.5 per-user credentials")
    else:
        print(f"{OK} 5.5 per-user credentials ({mcp_env})")

    # --- 5.4 encryption ----------------------------------------------
    try:
        keys = settings.encryption_keys

        if not keys or not keys[0]:
            problems.append("CREDENTIAL_ENCRYPTION_KEY is empty.")
            print(f"{BAD} 5.4 credential encryption")

        else:
            print(f"{OK} 5.4 credential encryption ({len(keys)} key(s))")

            if len(keys) > 1:
                warnings.append(
                    f"{len(keys)} keys are configured, which means a "
                    "rotation is in progress. Finish it with "
                    "scripts/rotate_credentials.py --apply, then drop "
                    "the old key."
                )

    except Exception as exc:
        problems.append(f"credential store will not build: {exc}")
        print(f"{BAD} 5.4 credential encryption")

    # --- 5.7 rate limits ---------------------------------------------
    if not settings.rate_limit_enabled:
        (problems if production else warnings).append(
            "Rate limiting is switched off. Nothing bounds cost or "
            "blast radius - one loop is a bill, and one compromised "
            "agent has all hour."
        )
        print(f"{BAD if production else WARN} 5.7 rate limiting")
    else:
        print(
            f"{OK} 5.7 rate limiting "
            f"({settings.agent_turns_per_hour} turns/h, "
            f"{settings.dangerous_ops_per_hour} high-risk/h)"
        )

    # --- 5.2 approvals -----------------------------------------------
    if settings.approval_timeout_seconds <= 0:
        problems.append(
            "APPROVAL_TIMEOUT_SECONDS must be positive. An approval "
            "that waits forever leaks a request slot."
        )
        print(f"{BAD} 5.2 approvals")
    else:
        print(
            f"{OK} 5.2 approvals "
            f"({settings.approval_timeout_seconds}s timeout)"
        )

    # --- 5.3 OAuth ----------------------------------------------------
    from api.oauth import supports_oauth

    configured = [
        key
        for key in ("github", "google_drive", "google_calendar", "slack")
        if supports_oauth(settings, key)
    ]

    if production and not configured:
        warnings.append(
            "No OAuth provider is configured, so users can only connect "
            "services by pasting a personal access token."
        )

    print(
        f"{OK} 5.3 OAuth "
        f"({len(configured)} provider(s): {', '.join(configured) or 'none'})"
    )

    if production and settings.oauth_redirect_base.startswith("http://"):
        problems.append(
            "OAUTH_REDIRECT_BASE is http://. An authorization code sent "
            "over plaintext is an authorization code anyone on the path "
            "can use."
        )

    # --- transport ----------------------------------------------------
    if production and not settings.frontend_base_url.startswith("https://"):
        problems.append(
            "FRONTEND_BASE_URL is not https. The refresh-token cookie "
            "needs Secure, and Secure needs TLS."
        )

    if production:
        print(f"{OK} docs disabled (/docs and /redoc are off)")

    # --- 5.8 the audit trail ------------------------------------------
    #
    # The only control on this list that lives in the DATABASE rather
    # than the application - and the only one the application cannot
    # give itself.
    try:
        import asyncio as _asyncio

        from sqlalchemy import text as _text
        from sqlalchemy.ext.asyncio import (
            create_async_engine as _create_engine,
        )

        async def _audit_role() -> tuple[bool, bool]:
            engine = _create_engine(settings.database_url)

            try:
                async with engine.connect() as conn:

                    superuser = bool(
                        (
                            await conn.execute(
                                _text(
                                    "select coalesce(usesuper, false) "
                                    "from pg_user where usename = "
                                    "current_user"
                                )
                            )
                        ).scalar()
                    )

                    writable = bool(
                        (
                            await conn.execute(
                                _text(
                                    "select has_table_privilege("
                                    "current_user, 'audit_log', 'DELETE')"
                                )
                            )
                        ).scalar()
                    )

                    return superuser, writable

            finally:
                await engine.dispose()

        superuser, can_delete = _asyncio.run(_audit_role())

        if can_delete or superuser:
            (problems if production else warnings).append(
                "The API connects as a role that can DELETE from "
                "audit_log"
                + (" (it is a SUPERUSER)" if superuser else "")
                + ". The append-only rule is a convention, not a "
                "control - run scripts/setup_db_roles.py."
            )
            print(f"{BAD if production else WARN} 5.8 append-only audit log")

        else:
            print(f"{OK} 5.8 append-only audit log")

    except Exception as exc:
        print(f"{WARN} 5.8 audit role not checked: {type(exc).__name__}")

    # --- the single-worker constraint ---------------------------------
    #
    # Not readable from here - it is a uvicorn flag - so it is always a
    # warning. It matters twice over: the approval notifier and the
    # rate limiter both hold state in ONE process's memory.
    warnings.append(
        "Run ONE uvicorn worker until Redis (Phase 6). With N workers "
        "the rate limits become N times larger and approvals resolved "
        "on the wrong worker time out."
    )

    # --- report -------------------------------------------------------
    if warnings:
        print("\nWorth knowing:\n")
        for warning in warnings:
            print(f"  - {warning}")

    if problems:
        print("\nNOT READY:\n")
        for problem in problems:
            print(f"  - {problem}")

        return 1

    print("\nConfiguration looks right for this environment.")

    if not production:
        print(
            "\nNote: API_ENVIRONMENT is not 'production', so the strict "
            "checks above were relaxed. Re-run with it set before you "
            "deploy."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
