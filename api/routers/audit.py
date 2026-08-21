from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, tuple_

from api.audit import AuditAction
from api.db.models import AuditLog
from api.deps import CurrentUser, DbDep
from api.pagination import Cursor, InvalidCursor, build_page
from api.schemas.audit import AuditEntryRead, AuditPage


"""
The user's own activity trail (Phase 5.9 surface for 5.8).

WHY THIS EXISTS AS A SCREEN AND NOT JUST A TABLE

Everything Phase 5 built is invisible by design - a permission that
holds, a token that stays encrypted, a limit that has not been reached.
The only way a user can confirm any of it is to see the record.

"Did anything happen to my account that I did not do?" is a question
somebody should be able to answer for themselves, without asking you
to run a query.

SCOPED TO THE CALLER, ALWAYS. An audit trail is a list of everything
somebody did, which makes it exactly the wrong thing to leak across
accounts.
"""


router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=AuditPage)
async def list_activity(
    current_user: CurrentUser,
    session: DbDep,
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> AuditPage:
    """
    Everything this user has done, or that was done to their account.

    Filtered by CATEGORY rather than by raw action, because "security"
    is a word a user has; "scope.granted" is not.
    """

    stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == current_user.id)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(limit + 1)
    )

    if category:
        actions = [a for a in AuditAction if _category(a.value) == category]

        if not actions:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown category {category!r}.",
            )

        stmt = stmt.where(AuditLog.action.in_([a.value for a in actions]))

    if cursor:
        try:
            position = Cursor.decode(cursor)

        except InvalidCursor:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Malformed cursor.",
            ) from None

        stmt = stmt.where(
            tuple_(AuditLog.occurred_at, AuditLog.id)
            < (position.created_at, position.row_id)
        )

    rows = list(await session.scalars(stmt))

    page, next_cursor, has_more = build_page(
        rows, limit, key=lambda r: (r.occurred_at, r.id)
    )

    return AuditPage(
        items=[_read(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _read(row: AuditLog) -> AuditEntryRead:

    return AuditEntryRead(
        id=row.id,
        action=row.action,
        label=_label(row),
        category=_category(row.action),
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        actor_ip=row.actor_ip,
        request_id=row.request_id,
        occurred_at=row.occurred_at,
        metadata=dict(row.meta or {}),
    )


def _category(action: str) -> str:
    """
    The four groups a person actually thinks in.

    Derived from the action prefix rather than stored, so a new action
    lands in the right group without a migration - and a new PREFIX
    lands in "other", visibly, rather than silently vanishing from a
    filter.
    """

    head = action.split(".")[0]

    if head in {"login", "logout", "user", "token"}:
        return "sign-in"

    if head in {"scope", "tool", "agent"}:
        return "permissions"

    if head in {"plugin"}:
        return "connections"

    if head in {"approval"}:
        return "approvals"

    return "other"


def _label(row: AuditLog) -> str:
    """
    One line, written for a human.

    Built server-side so the vocabulary lives in ONE place. Doing it in
    the browser would mean twenty-six strings duplicated in TypeScript,
    drifting the first time an action is added.
    """

    meta = row.meta or {}

    scope = meta.get("scope")
    tool = meta.get("tool_name")
    plugin = meta.get("plugin")

    known = {
        "login.succeeded": "Signed in",
        "login.failed": "Failed sign-in attempt",
        "login.blocked": "Sign-in blocked - too many attempts",
        "logout": "Signed out",
        "user.registered": "Account created",
        "token.reuse_detected": "A used session token was replayed",
        "scope.granted": f"Granted permission {scope}",
        "scope.revoked": f"Revoked permission {scope}",
        "tool.enabled": f"Enabled tool {tool}",
        "tool.disabled": f"Disabled tool {tool}",
        "agent.created": f"Created agent {meta.get('name', '')}".strip(),
        "agent.archived": f"Archived agent {meta.get('name', '')}".strip(),
        "plugin.connected": f"Connected {plugin}",
        "plugin.disconnected": f"Disconnected {plugin}",
        "plugin.refresh_failed": f"Could not refresh {plugin}",
        "approval.requested": f"Approval requested for {tool}",
        "approval.approved": f"You approved {tool}",
        "approval.denied": f"You denied {tool}",
        "approval.expired": f"Approval for {tool} expired unanswered",
        "staff.access": "Support accessed your data",
    }

    # The fallback is the raw action, never a blank. An unlabelled row
    # that renders as an empty line is a row the user cannot see - and
    # this table exists to be seen.
    return known.get(row.action, row.action)
