from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from api.approvals import ApprovalStatus
from api.audit import AuditAction, ResourceType
from api.db.models import PendingApproval
from api.notifier import ApprovalNotifier
from api.pagination import Cursor, build_page
from api.schemas.approval import ApprovalPage, ApprovalRead
from api.services import audit_service


"""
Reading and resolving approvals.

This is the OTHER side of api/approvals.py. That module is the turn
asking a question; this one is a human answering it, from a completely
separate HTTP request with its own session.

The two never share memory - only the row and, as a wakeup hint, the
notifier. Everything that matters is decided from the database, so a
lost notification costs a delay and never a wrong decision.
"""


class ApprovalError(Exception):
    """Base for approval failures."""


class ApprovalNotFound(ApprovalError):
    """
    No such approval FOR THIS USER.

    Does not distinguish "does not exist" from "belongs to someone
    else", for the same reason AgentNotFound does not: the router
    answers 404 either way, and a 403 would confirm that an id is real.

    On this endpoint that matters more than most. An approval id is a
    permission to make something happen - confirming one exists tells
    an attacker exactly which id is worth guessing at.
    """


class ApprovalAlreadyResolved(ApprovalError):
    """
    Someone - or the clock - already answered this.

    A 409, never a silent success. A replayed click on an expired
    approval that answered "200 OK" would tell the user their action
    went through when nothing ran.
    """


def _to_read(record: PendingApproval) -> ApprovalRead:

    now = datetime.now(timezone.utc)

    remaining = int((record.expires_at - now).total_seconds())

    return ApprovalRead(
        id=record.id,
        conversation_id=record.conversation_id,
        message_id=record.message_id,
        agent_id=record.agent_id,
        tool_name=record.tool_name,
        operation=record.operation,
        risk_level=record.risk_level,
        arguments=dict(record.arguments or {}),
        status=record.status,
        created_at=record.created_at,
        expires_at=record.expires_at,
        resolved_at=record.resolved_at,
        resolved_by=record.resolved_by,
        seconds_remaining=max(0, remaining) if record.is_pending else 0,
    )


async def _owned(
    session: AsyncSession,
    user_id: uuid.UUID,
    approval_id: uuid.UUID,
) -> PendingApproval:
    """
    Load an approval, scoped to its owner.

    Ownership in the WHERE clause, not an `if` after the fetch. This is
    the query behind the milestone test

        POST /api/approvals/{someone_elses_id}/approve  -> 404

    and the reason that test passes is this line, not the router.
    """

    record = await session.scalar(
        select(PendingApproval).where(
            PendingApproval.id == approval_id,
            PendingApproval.user_id == user_id,
        )
    )

    if record is None:
        raise ApprovalNotFound(str(approval_id))

    return record


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


async def list_approvals(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    status: str | None = "pending",
    limit: int = 20,
    cursor: str | None = None,
) -> ApprovalPage:
    """
    This user's approvals, newest first.

    Defaults to pending only - the inbox case, which the partial index
    on pending_approvals serves directly. Passing status=None returns
    the history instead.
    """

    stmt = (
        select(PendingApproval)
        .where(PendingApproval.user_id == user_id)
        .order_by(
            PendingApproval.created_at.desc(),
            PendingApproval.id.desc(),
        )
        .limit(limit + 1)
    )

    if status:
        stmt = stmt.where(PendingApproval.status == status)

    if cursor:
        position = Cursor.decode(cursor)

        stmt = stmt.where(
            tuple_(PendingApproval.created_at, PendingApproval.id)
            < (position.created_at, position.row_id)
        )

    rows = list(await session.scalars(stmt))

    page, next_cursor, has_more = build_page(
        rows,
        limit,
        key=lambda r: (r.created_at, r.id),
    )

    return ApprovalPage(
        items=[_to_read(r) for r in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_approval(
    session: AsyncSession,
    user_id: uuid.UUID,
    approval_id: uuid.UUID,
) -> ApprovalRead:

    return _to_read(await _owned(session, user_id, approval_id))


# ---------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------


async def resolve(
    session: AsyncSession,
    notifier: ApprovalNotifier,
    user_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    approved: bool,
    ip_address: str | None = None,
) -> ApprovalRead:
    """
    Answer one approval, and wake the turn that is waiting.

    THE ORDER IS THE CONTRACT

        1. load, scoped to the owner        (404 for anyone else)
        2. reject anything already resolved (409, never a silent 200)
        3. reject anything past expires_at  (the window has closed)
        4. UPDATE + COMMIT                  (the decision is now real)
        5. notify                           (only a hint to look again)

    Notifying before committing would be a race the turn usually wins:
    it wakes, re-reads the row inside its own transaction, still sees
    'pending', and denies a call the user just approved.

    Step 3 uses expires_at from the ROW, not a recomputed deadline. The
    waiting turn and this request must agree about when the window
    closed, and two processes doing their own arithmetic on their own
    clocks eventually will not.
    """

    record = await _owned(session, user_id, approval_id)

    if not record.is_pending:
        raise ApprovalAlreadyResolved(record.status)

    now = datetime.now(timezone.utc)

    if record.expires_at <= now:
        # The turn's own timer will have given up too - but this
        # request may well arrive first, and it must not authorise
        # something nobody is waiting for any more.
        record.status = str(ApprovalStatus.EXPIRED)
        record.resolved_at = now

        await session.commit()

        raise ApprovalAlreadyResolved(str(ApprovalStatus.EXPIRED))

    record.status = str(
        ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
    )
    record.resolved_at = now
    record.resolved_by = user_id

    # Same transaction as the decision. "Who approved sharing that
    # document?" must be answerable next year, and an audit row written
    # separately is one that can be missing for the single event
    # somebody is investigating.
    audit_service.record(
        session,
        (
            AuditAction.APPROVAL_APPROVED
            if approved
            else AuditAction.APPROVAL_DENIED
        ),
        user_id=record.user_id,
        actor_user_id=user_id,
        resource_type=ResourceType.AGENT,
        resource_id=record.agent_id,
        ip_address=ip_address,
        tool_name=record.tool_name,
        approval_id=str(record.id),
    )

    await session.commit()

    # AFTER the commit. The notifier is only a hint to go and look, and
    # by now there is something committed to look at.
    notifier.notify(record.id)

    return _to_read(record)


async def expire_stale(
    session: AsyncSession,
    *,
    limit: int = 500,
) -> int:
    """
    Mark approvals whose window closed while nobody was waiting.

    Normally the waiting turn expires its own row. This exists for the
    case where it cannot: the worker was restarted, or the turn died,
    and the row would otherwise sit 'pending' forever - showing in the
    user's inbox as a question that can never be answered.

    Not scheduled yet. Phase 6 runs it on a timer; today it is here so
    the recovery path exists and is tested.
    """

    now = datetime.now(timezone.utc)

    rows = list(
        await session.scalars(
            select(PendingApproval)
            .where(
                PendingApproval.status == str(ApprovalStatus.PENDING),
                PendingApproval.expires_at <= now,
            )
            .limit(limit)
        )
    )

    for record in rows:
        record.status = str(ApprovalStatus.EXPIRED)
        record.resolved_at = now

    if rows:
        await session.flush()

    return len(rows)
