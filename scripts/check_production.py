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
    from api.preflight import advisories, problems, run_checks

    settings = get_settings()

    production = settings.is_production

    print(f"environment : {settings.api_environment}\n")

    # EVERY SYNCHRONOUS CHECK NOW LIVES IN api/preflight.py.
    #
    # This script used to own them, which meant the application had no
    # way to enforce the same rules at startup without a second copy -
    # and two copies of a security check drift. The list moved; this
    # file kept the job it is actually good at: printing a report a
    # human reads before a deploy.
    checks = run_checks(settings)

    for check in checks:
        mark = OK if check.ok else (BAD if production else WARN)

        print(f"{mark} {check.name}" + (f" ({check.detail})" if check.detail else ""))

    found = problems(checks)
    notes = advisories(checks)

    if production:
        print(f"{OK} docs disabled (/docs and /redoc are off)")

    # --- 5.8 the audit trail ------------------------------------------
    #
    # STAYS HERE, and is the reason this script still exists separately.
    # It performs I/O - a real connection and two catalogue queries - so
    # it cannot run inside the synchronous create_app() path the way
    # everything above now does.
    #
    # It is also the only control on the list that lives in the DATABASE
    # rather than the application, and therefore the only one the
    # application cannot grant itself.
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
            message = (
                "The API connects as a role that can DELETE from "
                "audit_log"
                + (" (it is a SUPERUSER)" if superuser else "")
                + ". The append-only rule is a convention, not a "
                "control - run scripts/setup_db_roles.py."
            )

            (found if production else notes).append(message)

            print(f"{BAD if production else WARN} 5.8 append-only audit log")

        else:
            print(f"{OK} 5.8 append-only audit log")

    except Exception as exc:
        print(f"{WARN} 5.8 audit role not checked: {type(exc).__name__}")

    # --- report -------------------------------------------------------
    if notes:
        print("\nWorth knowing:\n")
        for note in notes:
            print(f"  - {note}")

    if found:
        print("\nNOT READY:\n")
        for problem in found:
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
