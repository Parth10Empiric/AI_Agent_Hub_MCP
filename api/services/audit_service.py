from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.audit import TRANSACTIONAL_ACTIONS, AuditAction, ResourceType
from api.db.models import AuditLog
from api.pagination import Cursor, build_page
from api.request_context import current_ip, current_request_id
from core.logging import get_logger


logger = get_logger(__name__)


"""
Writing the audit trail (Phase 5.8).

TWO WAYS TO WRITE, AND THE CHOICE IS NOT STYLE

    record()   commits WITH the thing it describes
    observe()  never blocks, never raises

Phase5.md says "the audit path must not fail the request". Phases 5.1
and 5.2 did the opposite - the audit row went in the same transaction
as the grant - and both are right, about different events:

    IF THE EVENT *IS* THE STATE CHANGE, they commit together.
    A scope grant whose audit row is missing is a lie about
    permissions, and a log that is missing the one event somebody is
    investigating is worse than no log, because it is believed.

    IF THE EVENT MERELY OBSERVES SOMETHING, it must never be able to
    fail what it observed. Refusing a successful login because the
    audit table filled a disk would lock everybody out to protect a
    record of them getting in.

TRANSACTIONAL_ACTIONS in api/audit.py is that list, and record()
enforces it: passing an observational action to record() is a
programming error and says so.
"""


def entry(
    action: AuditAction | str,
    *,
    user_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: ResourceType | str | None = None,
    resource_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    **metadata: Any,
) -> AuditLog:
    """
    Build one row, with the request context filled in automatically.

    `ip_address` and `request_id` fall back to ContextVars set by the
    middleware, so a service three layers down records them without
    every function on the way threading them through - the same trick
    core/tenancy.py uses for credentials.

    IDENTIFIERS ONLY in `metadata`. Which file, not the file; which
    channel, not the message. This table is append-only and kept for a
    year, so anything written here is something you have committed to
    keeping.
    """

    return AuditLog(
        action=str(action),
        user_id=user_id,
        actor_user_id=actor_user_id,
        resource_type=str(resource_type) if resource_type else None,
        resource_id=resource_id,
        # The explicit value wins - a login records the address it was
        # attempted from even if that differs from the context.
        actor_ip=(ip_address or current_ip() or "")[:45] or None,
        request_id=(request_id or current_request_id() or "")[:32] or None,
        meta={k: v for k, v in metadata.items() if v is not None},
    )


def record(session: AsyncSession, action: AuditAction, **fields: Any) -> None:
    """
    Add an audit row to the CALLER'S transaction.

    Deliberately does not flush or commit: it joins whatever the caller
    is already doing, so the event and the change it describes succeed
    or fail as one.

    Only for actions in TRANSACTIONAL_ACTIONS. Using it for a login
    would make a full audit table an outage.
    """

    if action not in TRANSACTIONAL_ACTIONS:
        raise ValueError(
            f"{action} is observational - use observe(), which cannot "
            f"fail the request it is recording."
        )

    session.add(entry(action, **fields))


async def observe(
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    action: AuditAction,
    **fields: Any,
) -> None:
    """
    Record something that happened, without being able to break it.

    ITS OWN SESSION, not the caller's. Two reasons, and the second is
    the subtle one:

      1. The caller may be about to roll back - a failed login rolls
         nothing back, but a failed OAuth refresh might - and the
         record of the attempt must survive that.

      2. Joining the caller's transaction would mean a broken audit
         write poisons it. The whole point of this function is that it
         cannot.

    A FAILURE HERE RAISES AN ALERT, per Phase5.md rule 4. Silently
    swallowing it would be the worst outcome: an audit log that stopped
    recording weeks ago and nobody noticed is indistinguishable from
    one where nothing happened.
    """

    if sessionmaker is None:
        # No session factory - the CLI, a test. Nothing to write to,
        # and refusing to run would be absurd.
        return

    try:
        async with sessionmaker() as session:
            session.add(entry(action, **fields))
            await session.commit()

    except Exception:
        # ERROR, not warning. This is the one log line that means
        # "your audit trail has a hole in it", and it must be loud
        # enough to page somebody in a real deployment.
        logger.error(
            "AUDIT WRITE FAILED for %s - the trail is now incomplete",
            action,
            exc_info=True,
        )


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


async def list_for_resource(
    session: AsyncSession,
    resource_type: ResourceType | str,
    resource_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[AuditLog], str | None, bool]:
    """
    The 2am query: everything that happened to one thing, in order.

    `user_id` scopes it to a tenant. Optional in the signature and
    mandatory in practice - every caller passes it, because an audit
    trail is exactly the wrong thing to leak across accounts.
    """

    stmt = (
        select(AuditLog)
        .where(
            AuditLog.resource_type == str(resource_type),
            AuditLog.resource_id == resource_id,
        )
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(limit + 1)
    )

    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)

    if cursor:
        position = Cursor.decode(cursor)

        stmt = stmt.where(
            tuple_(AuditLog.occurred_at, AuditLog.id)
            < (position.created_at, position.row_id)
        )

    rows = list(await session.scalars(stmt))

    return build_page(rows, limit, key=lambda r: (r.occurred_at, r.id))


async def purge_older_than(
    session: AsyncSession,
    *,
    days: int = 400,
) -> int:
    """
    Delete rows past the retention period.

    400 days, not 365: "retain 12 months minimum" means the boundary
    has to be ON THE FAR SIDE of twelve months, and a job that trims to
    exactly a year leaves you non-compliant the moment it runs late.

    NEEDS A ROLE THAT CAN DELETE. The application role deliberately
    cannot (see scripts/setup_db_roles.py) - which is the point, and
    which means this runs as the owner, on a schedule, not from a
    request.
    """

    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await session.execute(
        delete(AuditLog).where(AuditLog.occurred_at < cutoff)
    )

    return result.rowcount or 0
