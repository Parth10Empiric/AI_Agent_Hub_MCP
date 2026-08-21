from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.deps import ApprovalNotifierDep, CurrentUser, DbDep
from api.pagination import InvalidCursor
from api.schemas.approval import ApprovalPage, ApprovalRead
from api.services import approval_service
from api.services.approval_service import (
    ApprovalAlreadyResolved,
    ApprovalNotFound,
)


"""
The human side of an approval.

A turn is parked inside the executor waiting for one of these requests.
Everything here is therefore load-bearing in a way an ordinary CRUD
router is not: a bug that resolves the wrong row runs a tool call
somebody else authorised.

Hence the same three rules as the permissions router, plus one:

    1. ownership is a WHERE clause, never an `if`
    2. a wrong owner gets 404, never 403
    3. every resolution writes an audit row in the same transaction
    4. resolving twice is a 409, never a silent success
"""


router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Approval not found.",
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=ApprovalPage)
async def list_approvals(
    current_user: CurrentUser,
    session: DbDep,
    status_filter: str | None = Query(default="pending", alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ApprovalPage:
    """
    The approvals inbox.

    Defaults to pending, because that is the only list anyone opens
    this page to see. `?status=` (empty) returns the full history.
    """

    try:
        return await approval_service.list_approvals(
            session,
            current_user.id,
            status=status_filter or None,
            limit=limit,
            cursor=cursor,
        )

    except InvalidCursor:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Malformed cursor.",
        ) from None


@router.get("/{approval_id}", response_model=ApprovalRead)
async def get_approval(
    approval_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
) -> ApprovalRead:
    """
    One approval, with the real arguments.

    This is what the dialog renders. A user who has refreshed the page
    or opened a second tab has lost the SSE frame that announced it,
    and must still be able to see the question.
    """

    try:
        return await approval_service.get_approval(
            session, current_user.id, approval_id
        )

    except ApprovalNotFound:
        raise _not_found() from None


@router.post("/{approval_id}/approve", response_model=ApprovalRead)
async def approve(
    approval_id: uuid.UUID,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    notifier: ApprovalNotifierDep,
) -> ApprovalRead:
    """
    Say yes, and wake the waiting turn.

    Approving is NOT the last word. The turn re-checks the agent's
    scopes before it runs the tool, so a permission revoked while the
    dialog was open still stops the call. This endpoint records consent;
    it does not grant capability.
    """

    return await _resolve(
        request, session, notifier, current_user.id, approval_id, True
    )


@router.post("/{approval_id}/deny", response_model=ApprovalRead)
async def deny(
    approval_id: uuid.UUID,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    notifier: ApprovalNotifierDep,
) -> ApprovalRead:
    """
    Say no.

    Explicit rather than "just let it expire". Denying wakes the turn
    immediately, so the agent can tell the user it was refused instead
    of sitting silent for five minutes.
    """

    return await _resolve(
        request, session, notifier, current_user.id, approval_id, False
    )


async def _resolve(
    request: Request,
    session,
    notifier,
    user_id: uuid.UUID,
    approval_id: uuid.UUID,
    approved: bool,
) -> ApprovalRead:

    try:
        return await approval_service.resolve(
            session,
            notifier,
            user_id,
            approval_id,
            approved=approved,
            ip_address=_client_ip(request),
        )

    except ApprovalNotFound:
        raise _not_found() from None

    except ApprovalAlreadyResolved as exc:
        # 409 Conflict. The request was well formed; the resource is
        # not in a state that can accept it. The detail names the
        # status so the UI can say "this expired" rather than the
        # useless "something went wrong".
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This approval is already {exc}.",
        ) from None
