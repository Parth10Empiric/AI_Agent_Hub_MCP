from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.deps import AgentEngineDep, CurrentUser, DbDep
from api.pagination import InvalidCursor
from api.schemas.permission import (
    AgentScopes,
    PermissionAuditPage,
    ScopeGrant,
)
from api.scopes import InvalidScope
from api.services import permission_service
from api.services.agent_service import AgentNotFound


"""
Scope management for one agent.

These four endpoints are where a user says what an agent may do. Every
row the executor later trusts was written here, which makes this the
most security-sensitive router in the API - so three rules hold
throughout:

    1. ownership is proved by a WHERE clause, never an `if`
    2. a wrong owner gets 404, never 403
    3. every change writes an audit row in the SAME transaction
"""


router = APIRouter(prefix="/api/agents", tags=["permissions"])


def _not_found() -> HTTPException:
    """
    404, never 403 - the same rule as the agents router.

    403 means "this exists and you may not have it", which confirms the
    id is real. On a PERMISSIONS endpoint that confirmation is worth
    more to an attacker than anywhere else in the API: it is how they
    map which agents are worth attacking.
    """

    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Agent not found.",
    )


def _client_ip(request: Request) -> str | None:
    """
    The caller's IP, for the audit row.

    request.client is None for some ASGI transports (notably the test
    client), so it is checked rather than assumed - an audit write must
    never be the thing that raises.
    """

    return request.client.host if request.client else None


@router.get("/{agent_id}/scopes", response_model=AgentScopes)
async def list_scopes(
    agent_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentScopes:
    """
    What this agent is granted, and what it could be.

    Both halves in one response so the UI never renders ticks from a
    stale second request.
    """

    try:
        return await permission_service.list_scopes(
            session, engine, current_user.id, agent_id
        )

    except AgentNotFound:
        raise _not_found() from None


@router.post("/{agent_id}/scopes", response_model=AgentScopes)
async def grant_scope(
    agent_id: uuid.UUID,
    payload: ScopeGrant,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentScopes:
    """
    Grant one scope.

    Idempotent: granting twice changes nothing and writes no second
    audit row, so a double-clicked button cannot produce a history that
    reads like the user changed their mind.
    """

    try:
        return await permission_service.grant_scope(
            session,
            engine,
            current_user.id,
            agent_id,
            payload.scope,
            actor_user_id=current_user.id,
            ip_address=_client_ip(request),
        )

    except AgentNotFound:
        raise _not_found() from None

    except InvalidScope as exc:
        # 422, not 400: the request was understood and its shape was
        # wrong. The message names the scope, because "invalid scope"
        # with no detail is how a user ends up believing the UI is
        # broken when they simply typed "issues" for "issue".
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from None


@router.delete("/{agent_id}/scopes", response_model=AgentScopes)
async def revoke_scope(
    agent_id: uuid.UUID,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
    scope: str = Query(min_length=3, max_length=120),
) -> AgentScopes:
    """
    Revoke one scope.

    The scope travels as a QUERY parameter, not a path segment. Scope
    strings contain ":" and "*", and proxies, routers and clients
    disagree about escaping those inside a path - a rule that only
    sometimes reaches the server is not a security control.
    """

    try:
        return await permission_service.revoke_scope(
            session,
            engine,
            current_user.id,
            agent_id,
            scope,
            actor_user_id=current_user.id,
            ip_address=_client_ip(request),
        )

    except AgentNotFound:
        raise _not_found() from None


@router.get("/{agent_id}/audit", response_model=PermissionAuditPage)
async def list_audit(
    agent_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> PermissionAuditPage:
    """
    This agent's permission history, newest first.

    Read-only by construction: no endpoint in this API updates or
    deletes an audit row, and Phase 6 revokes UPDATE and DELETE on the
    table at the database role level so the guarantee does not rest on
    nobody ever writing one.
    """

    try:
        return await permission_service.list_audit(
            session,
            current_user.id,
            agent_id,
            limit=limit,
            cursor=cursor,
        )

    except AgentNotFound:
        raise _not_found() from None

    except InvalidCursor:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Malformed cursor.",
        ) from None
