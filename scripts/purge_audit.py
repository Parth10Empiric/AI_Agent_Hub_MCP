"""
Trim the audit log past its retention period (Phase 5.8).

    python scripts/purge_audit.py                # show what would go
    python scripts/purge_audit.py --apply

WHY 400 DAYS AND NOT 365

Phase5.md says "retain 12 months MINIMUM". A job that trims to exactly
twelve months leaves you non-compliant the moment it runs late, or the
moment somebody asks about an event from thirteen months ago that was
deleted yesterday. The boundary belongs on the far side of the
requirement.

WHY THIS RUNS AS THE OWNER, NOT AS THE APP

The application role deliberately cannot DELETE from audit_log - that
is what makes the log append-only rather than merely append-mostly
(scripts/setup_db_roles.py). So retention runs on a schedule, as the
owner, from outside a request. That separation is the feature: nothing
reachable from the API can remove a row.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from api.db.models import AuditLog  # noqa: E402
from api.services import audit_service  # noqa: E402
from api.settings import get_settings  # noqa: E402


DEFAULT_DAYS = 400


async def purge(days: int, apply: bool) -> int:

    # The OWNER url, because the app role cannot delete here. Falls
    # back to DATABASE_URL for a deployment that has not split the
    # roles yet.
    url = os.getenv("ALEMBIC_DATABASE_URL") or get_settings().database_url

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        async with maker() as session:

            total = await session.scalar(
                select(func.count()).select_from(AuditLog)
            )

            stale = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.occurred_at < cutoff)
            )

            print(f"retention : {days} days")
            print(f"cutoff    : {cutoff.date()}")
            print(f"rows      : {total} total, {stale} older than cutoff")

            if not stale:
                print("\nNothing to remove.")
                return 0

            if not apply:
                print("\nDry run. Re-run with --apply.")
                return 0

            removed = await audit_service.purge_older_than(
                session, days=days
            )

            await session.commit()

            print(f"\nremoved: {removed}")

    finally:
        await engine.dispose()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)

    args = parser.parse_args()

    if args.days < 365:
        print(
            f"Refusing: {args.days} days is under the 12-month minimum.\n"
            "If that is genuinely intended, change the check in this file "
            "deliberately rather than passing a flag."
        )
        return 1

    return asyncio.run(purge(args.days, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
