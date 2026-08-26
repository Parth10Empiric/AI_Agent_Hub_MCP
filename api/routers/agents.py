from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from api.deps import AgentEngineDep, CurrentUser, DbDep
from api.schemas.agent import (
    AgentCreate,
    AgentDetail,
    AgentServicesUpdate,
    AgentSummary,
    AgentToolsUpdate,
    AgentUpdate,
)
from api.services import agent_service
from api.services.agent_service import AgentNotFound, UnknownPlugin


router = APIRouter(prefix="/api/agents", tags=["agents"])


def _not_found() -> HTTPException:
    """
    404, never 403.

    403 would mean "this exists and you may not have it", which
    confirms the id is real. For a resource keyed by an id in the URL,
    that confirmation is the whole prize an attacker is enumerating
    for. 404 says nothing.
    """

    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Agent not found.",
    )


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
    include_archived: bool = Query(default=False),
) -> list[AgentSummary]:
    return await agent_service.list_agents(
        session,
        engine,
        current_user.id,
        include_archived=include_archived,
    )


@router.post("", response_model=AgentDetail, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentDetail:
    """
    Create an agent.

    Tools not named in the payload get the Phase 2 default: reads on,
    writes off. The agent is safe the moment it exists.
    """

    try:
        return await agent_service.create_agent(
            session, engine, current_user.id, payload
        )

    except UnknownPlugin as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown plugin(s): {exc}",
        ) from None


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(
    agent_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentDetail:
    try:
        return await agent_service.get_agent(
            session, engine, current_user.id, agent_id
        )

    except AgentNotFound:
        raise _not_found() from None


@router.patch("/{agent_id}", response_model=AgentDetail)
async def update_agent(
    agent_id: uuid.UUID,
    payload: AgentUpdate,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentDetail:
    try:
        return await agent_service.update_agent(
            session, engine, current_user.id, agent_id, payload
        )

    except AgentNotFound:
        raise _not_found() from None


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_agent(
    agent_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
) -> Response:
    """
    Archive, not delete.

    DELETE is still the right verb - it is what the user means - but
    the row survives so conversations and execution history keep
    pointing at something real.
    """

    try:
        await agent_service.archive_agent(session, current_user.id, agent_id)

    except AgentNotFound:
        raise _not_found() from None

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{agent_id}/tools", response_model=AgentDetail)
async def get_tools(
    agent_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentDetail:
    try:
        return await agent_service.get_agent(
            session, engine, current_user.id, agent_id
        )

    except AgentNotFound:
        raise _not_found() from None


@router.put("/{agent_id}/tools", response_model=AgentDetail)
async def set_tools(
    agent_id: uuid.UUID,
    payload: AgentToolsUpdate,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentDetail:
    """
    Set which tools this agent may use.

    This endpoint is where a user switches individual tools on. It is
    only HALF of the permission model: since Phase 5.1 a tool also
    needs a scope granted through /agents/{id}/scopes, and both are
    checked independently by DatabaseScopePolicy.

    Ticking a write tool here therefore does NOT by itself give the
    agent write access - which is the point. Two deliberate actions,
    not one, stand between "create agent" and "may modify a client's
    repository".
    """

    try:
        return await agent_service.set_tools(
            session,
            engine,
            current_user.id,
            agent_id,
            payload.tools,
            # Phase 5.1: enabling a tool is a permission change, so it
            # is audited with who did it and from where.
            actor_user_id=current_user.id,
            ip_address=request.client.host if request.client else None,
        )

    except AgentNotFound:
        raise _not_found() from None


@router.put("/{agent_id}/services", response_model=AgentDetail)
async def set_services(
    agent_id: uuid.UUID,
    payload: AgentServicesUpdate,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> AgentDetail:
    """
    Set which services this agent draws tools from.

    THE WHOLE SET, not a delta. The screen behind this is a list of
    ticks and one Save button, and a PUT is the only shape that cannot
    apply half of what the user chose.

    Adding a service writes its tool rows and seeds READ scopes for it -
    the same thing the create wizard does, because "add Google Drive to
    this agent" means the same thing whenever it is said. Removing one
    deletes its rows AND revokes its scopes, so nothing is left granted
    on a screen that no longer lists it.

    400 rather than 404 for an unknown service: the agent exists, the
    request is wrong.
    """

    try:
        return await agent_service.set_services(
            session,
            engine,
            current_user.id,
            agent_id,
            payload.services,
            actor_user_id=current_user.id,
            ip_address=request.client.host if request.client else None,
        )

    except AgentNotFound:
        raise _not_found() from None

    except UnknownPlugin as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown service: {exc}",
        ) from None
