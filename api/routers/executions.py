from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from api.deps import CurrentUser, DbDep
from api.pagination import InvalidCursor
from api.schemas.execution import ExecutionPage, ExecutionStats
from api.services import execution_service
from api.services.execution_service import InvalidFilter


router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.get("", response_model=ExecutionPage)
async def list_executions(
    current_user: CurrentUser,
    session: DbDep,
    # le=200 is a guard, not a preference. Without an upper bound a
    # client can ask for ?limit=1000000 and turn a page request into a
    # full table scan that holds a connection for minutes.
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="success | failed | denied",
    ),
    agent_id: uuid.UUID | None = Query(default=None),
    days: int | None = Query(default=None, ge=1, le=365),
) -> ExecutionPage:
    """
    This user's tool activity, newest first.

    `status` is aliased because `status` is also the name of the
    FastAPI module imported above for status codes. The query string
    keeps the name a user would expect; the Python parameter avoids the
    shadowing bug.
    """

    try:
        return await execution_service.list_executions(
            session,
            current_user.id,
            limit=limit,
            cursor=cursor,
            status=status_filter,
            agent_id=agent_id,
            days=days,
        )

    except InvalidFilter as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    except InvalidCursor:
        # 422, not 500. A malformed cursor is bad input, and it is
        # usually a stale bookmark rather than an attack.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid cursor.",
        ) from None


@router.get("/stats", response_model=ExecutionStats)
async def execution_stats(
    current_user: CurrentUser,
    session: DbDep,
    days: int = Query(default=7, ge=1, le=365),
) -> ExecutionStats:
    """
    Counts for the dashboard tiles.

    Declared BEFORE any "/{execution_id}" route would be. FastAPI
    matches in declaration order, so a path parameter registered first
    would swallow "/stats" and try to look up an execution with that
    id.
    """

    return await execution_service.get_stats(
        session, current_user.id, days=days
    )
