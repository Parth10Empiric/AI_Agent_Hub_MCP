"""
Make the audit log genuinely append-only (Phase 5.8).

    python scripts/setup_db_roles.py --check
    python scripts/setup_db_roles.py --apply --password '<strong>'

WHY THE APPLICATION MUST NOT OWN ITS OWN DATABASE

Right now this project connects as `postgres` - a SUPERUSER, and the
owner of every table. That means the rule the audit log depends on:

    INSERT only. No UPDATE, no DELETE.

is a convention the application imposes on itself, and a convention is
not a control. A superuser bypasses every permission check, and even a
plain owner can re-grant themselves whatever you revoked. An SQL
injection anywhere in the API could rewrite history, and the log would
have no way to show it.

    REVOKE UPDATE, DELETE ON audit_log FROM postgres;   -- does nothing

THE FIX IS A SECOND ROLE

    agenthub_app     what the API connects as. Full DML on every table
                     EXCEPT audit_log, where it may only INSERT and
                     SELECT.

    postgres         what Alembic connects as. Owns the schema, does
                     the DDL, and can delete rows when the retention
                     job runs.

Two credentials, two jobs. After this, the application cannot alter
history even if somebody finds a way to make it run arbitrary SQL.

WHAT YOU MUST DO AFTERWARDS

    DATABASE_URL          -> agenthub_app     (the API)
    ALEMBIC_DATABASE_URL  -> postgres         (migrations, retention)

Set the second BEFORE switching the first, or `alembic upgrade` starts
failing with permission errors that look like a broken migration.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from api.settings import get_settings  # noqa: E402


APP_ROLE = "agenthub_app"

OK = "  OK  "
BAD = " FAIL "


# Everything the API needs, and the one exception that matters.
#
# Written as explicit statements rather than a loop, because this is a
# file somebody will read while deciding whether to trust it.
GRANT_SQL = """
-- The database name is QUOTED. PostgreSQL folds unquoted
-- identifiers to lower case, so a database called Agent_Hub_MCP
-- becomes agent_hub_mcp and the grant fails on a database that
-- "does not exist".
GRANT CONNECT ON DATABASE "{database}" TO {role};
GRANT USAGE ON SCHEMA public TO {role};

GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public TO {role};

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};

-- Tables created by FUTURE migrations, so a new table is not silently
-- unreadable by the application until somebody remembers this file.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role};

-- THE ONE EXCEPTION. Everything above, minus the two verbs that can
-- rewrite the past.
REVOKE UPDATE, DELETE ON audit_log FROM {role};

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT ON TABLES TO {role};
"""


def _statements(script: str) -> list[str]:
    """
    Split a script into runnable statements.

    STRIPS LEADING COMMENTS rather than skipping chunks that begin with
    one, and that distinction was a real bug: splitting on ";" leaves
    every statement preceded by its own comment block, so a naive
    `startswith("--")` test skipped the statements instead of the
    comments.

    The one it skipped was the REVOKE - so the script reported success
    while leaving the application able to rewrite its own audit log.
    Exactly the failure this file exists to prevent, produced by the
    file itself.
    """

    statements: list[str] = []

    for chunk in script.split(";"):

        lines = [
            line
            for line in chunk.strip().splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]

        if lines:
            statements.append("\n".join(lines))

    return statements


async def check(url: str) -> int:

    engine = create_async_engine(url)

    problems: list[str] = []

    try:
        async with engine.connect() as conn:

            user = (await conn.execute(text("select current_user"))).scalar()

            superuser = (
                await conn.execute(
                    text(
                        "select coalesce(usesuper, false) from pg_user "
                        "where usename = current_user"
                    )
                )
            ).scalar()

            print(f"connected as : {user}")
            print(f"superuser    : {superuser}")

            exists = (
                await conn.execute(
                    text(
                        "select 1 from pg_roles where rolname = :role"
                    ),
                    {"role": APP_ROLE},
                )
            ).scalar()

            print(
                f"{OK if exists else BAD} role {APP_ROLE!r} "
                f"{'exists' if exists else 'does not exist'}"
            )

            if not exists:
                problems.append(
                    f"{APP_ROLE} does not exist - run with --apply."
                )

            else:
                can_delete = (
                    await conn.execute(
                        text(
                            "select has_table_privilege("
                            "  :role, 'audit_log', 'DELETE')"
                        ),
                        {"role": APP_ROLE},
                    )
                ).scalar()

                can_update = (
                    await conn.execute(
                        text(
                            "select has_table_privilege("
                            "  :role, 'audit_log', 'UPDATE')"
                        ),
                        {"role": APP_ROLE},
                    )
                ).scalar()

                can_insert = (
                    await conn.execute(
                        text(
                            "select has_table_privilege("
                            "  :role, 'audit_log', 'INSERT')"
                        ),
                        {"role": APP_ROLE},
                    )
                ).scalar()

                if can_delete or can_update:
                    problems.append(
                        f"{APP_ROLE} can still "
                        f"{'DELETE' if can_delete else ''}"
                        f"{'/' if can_delete and can_update else ''}"
                        f"{'UPDATE' if can_update else ''} audit_log."
                    )
                    print(f"{BAD} audit_log is not append-only")
                else:
                    print(f"{OK} audit_log is append-only for {APP_ROLE}")

                if not can_insert:
                    problems.append(
                        f"{APP_ROLE} cannot INSERT into audit_log - "
                        "nothing would be recorded at all."
                    )

            if superuser:
                print(
                    f"\n  NOTE  the API is connecting as a SUPERUSER, so "
                    f"the grant above does not apply to it.\n"
                    f"        Point DATABASE_URL at {APP_ROLE} to make it "
                    f"real."
                )

    finally:
        await engine.dispose()

    if problems:
        print("\nProblems:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    return 0


async def apply(url: str, password: str) -> int:

    engine = create_async_engine(url)

    database = url.rsplit("/", 1)[-1].split("?")[0]

    try:
        # AUTOCOMMIT: CREATE ROLE cannot run inside a transaction block
        # on some configurations, and a half-applied grant set is worse
        # than none.
        async with engine.connect() as conn:

            await conn.execution_options(isolation_level="AUTOCOMMIT")

            exists = (
                await conn.execute(
                    text("select 1 from pg_roles where rolname = :role"),
                    {"role": APP_ROLE},
                )
            ).scalar()

            if not exists:
                # The password is interpolated because PostgreSQL does
                # not accept a bind parameter in CREATE ROLE. It comes
                # from the command line, not from a request - but the
                # quote doubling is here so a password containing an
                # apostrophe cannot end the statement.
                safe = password.replace("'", "''")

                await conn.execute(
                    text(
                        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{safe}'"
                    )
                )
                print(f"{OK} created role {APP_ROLE}")

            else:
                print(f"{OK} role {APP_ROLE} already exists")

            for statement in _statements(
                GRANT_SQL.format(database=database, role=APP_ROLE)
            ):
                await conn.execute(text(statement))

            print(f"{OK} grants applied")

    finally:
        await engine.dispose()

    print(
        "\nNow, in this order:\n"
        f"  1. ALEMBIC_DATABASE_URL = the current (owner) URL\n"
        f"  2. DATABASE_URL         = the same URL with {APP_ROLE} and "
        f"its password\n"
        "\nStep 1 first, or migrations start failing with permission "
        "errors that look like a broken migration."
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--password", default="")

    args = parser.parse_args()

    url = get_settings().database_url

    if args.apply:
        if not args.password:
            print("--apply needs --password")
            return 1

        return asyncio.run(apply(url, args.password))

    return asyncio.run(check(url))


if __name__ == "__main__":
    raise SystemExit(main())
