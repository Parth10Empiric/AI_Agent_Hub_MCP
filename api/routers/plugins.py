from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from api.deps import AgentEngineDep, CredentialStoreDep, CurrentUser, DbDep
from api.schemas.plugin import (
    ConnectionRead,
    ConnectRequest,
    PluginDetail,
    PluginSummary,
)
from api.services import plugin_service
from api.services.plugin_service import UnknownPlugin


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _not_found(key: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No plugin named {key!r}.",
    )


@router.get("", response_model=list[PluginSummary])
async def list_plugins(
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> list[PluginSummary]:
    """
    Every service the MCP server currently exposes.

    Nothing here is hardcoded. Add a service to the MCP server, restart,
    and it appears with its tools, counts and risk levels already
    correct - no migration, no backend change.
    """

    return await plugin_service.list_catalogue(session, engine, current_user.id)


@router.get("/connections", response_model=list[ConnectionRead])
async def list_connections(
    current_user: CurrentUser,
    session: DbDep,
) -> list[ConnectionRead]:
    """
    This user's connections.

    Declared BEFORE /{key} on purpose. FastAPI matches routes in
    declaration order, so if /{key} came first it would capture
    "connections" as a plugin key and this endpoint would be
    unreachable - a 404 that looks like a bug in the service layer.
    """

    return await plugin_service.list_connections(session, current_user.id)


@router.get("/{key}", response_model=PluginDetail)
async def get_plugin(
    key: str,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> PluginDetail:
    try:
        return await plugin_service.get_plugin(
            session, engine, current_user.id, key
        )

    except UnknownPlugin:
        raise _not_found(key) from None


@router.post(
    "/{key}/connect",
    response_model=ConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def connect(
    key: str,
    payload: ConnectRequest,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
    store: CredentialStoreDep,
) -> ConnectionRead:
    """
    Store an encrypted credential for this service.

    Reconnecting replaces the credential rather than failing, which is
    what a user means when they paste a new token.
    """

    try:
        return await plugin_service.connect(
            session,
            engine,
            store,
            current_user.id,
            key,
            credential=payload.credential,
            account_label=payload.account_label,
            scopes=payload.scopes,
        )

    except UnknownPlugin:
        raise _not_found(key) from None


@router.delete("/{key}/connect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    key: str,
    current_user: CurrentUser,
    session: DbDep,
) -> Response:
    """
    Remove this user's connection to the service.

    404 when there was nothing to remove, so the client can tell "you
    are now disconnected" from "you were never connected" - a
    distinction that leaks nothing, because the query was already
    scoped to the caller.
    """

    removed = await plugin_service.disconnect(session, current_user.id, key)

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connection to {key!r}.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
