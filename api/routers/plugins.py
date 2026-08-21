from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse

from api.deps import (
    AgentEngineDep,
    CredentialStoreDep,
    CurrentUser,
    DbDep,
    SettingsDep,
)
from api.schemas.plugin import (
    ConnectionRead,
    ConnectRequest,
    OAuthStart,
    PluginDetail,
    PluginSummary,
)
from api.oauth import OAuthError, ProviderNotConfigured
from api.services import oauth_service, plugin_service
from api.services.oauth_service import InvalidState, OAuthFlowError
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
    settings: SettingsDep,
) -> list[PluginSummary]:
    """
    Every service the MCP server currently exposes.

    Nothing here is hardcoded. Add a service to the MCP server, restart,
    and it appears with its tools, counts and risk levels already
    correct - no migration, no backend change.
    """

    return await plugin_service.list_catalogue(
        session, engine, current_user.id, settings
    )


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
    settings: SettingsDep,
) -> PluginDetail:
    try:
        return await plugin_service.get_plugin(
            session, engine, current_user.id, key, settings
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


# ---------------------------------------------------------------------
# OAuth (Phase 5.3)
# ---------------------------------------------------------------------


@router.post("/{key}/oauth/start", response_model=OAuthStart)
async def oauth_start(
    key: str,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
    settings: SettingsDep,
    redirect_to: str | None = Query(default=None, max_length=512),
) -> OAuthStart:
    """
    Begin connecting a service.

    Returns the provider's consent URL for the client to navigate to.
    See OAuthStart for why this is not a 302.

    The `state` row written here is what makes the callback safe: it
    proves the callback belongs to a flow WE started, it carries the
    user's identity into a request that has no Authorization header,
    and it can only be redeemed once.
    """

    if not engine.registry.by_namespace(key):
        raise _not_found(key)

    try:
        url = await oauth_service.start(
            session,
            settings,
            current_user.id,
            key,
            redirect_to=redirect_to,
        )

    except ProviderNotConfigured as exc:
        # 503, not 400. The request was perfectly valid; the deployment
        # is unfinished. The message names the missing setting so
        # whoever runs this can fix it in one step.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from None

    from api.oauth import build_provider

    return OAuthStart(
        authorize_url=url,
        scopes=list(build_provider(settings, key).scopes),
    )


@router.get("/{key}/oauth/callback", include_in_schema=False)
async def oauth_callback(
    key: str,
    session: DbDep,
    settings: SettingsDep,
    store: CredentialStoreDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """
    Where the provider sends the browser back.

    DELIBERATELY NOT AUTHENTICATED, and it does not need to be.

    A browser following the provider's redirect sends no Authorization
    header, so CurrentUser cannot work here. The identity comes from
    the `state` row instead - which is exactly why that row must be
    unguessable, single-use and short-lived. Everything this endpoint
    trusts, it trusts because of oauth_states.

    ALWAYS REDIRECTS, never returns JSON. A human is looking at this
    response in their address bar; a JSON error body would strand them
    on a blank page with a raw payload.
    """

    target = f"{settings.frontend_base_url.rstrip('/')}/plugins"

    # The provider itself refused - the user clicked Cancel, or the app
    # is misconfigured on their side. Nothing was granted, so there is
    # nothing to clean up.
    if error:
        return RedirectResponse(
            f"{target}?error={quote(error)}&plugin={quote(key)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        connection, redirect_to = await oauth_service.complete(
            session,
            settings,
            store,
            key,
            state=state,
            code=code,
        )

        await session.commit()

    except InvalidState:
        return RedirectResponse(
            f"{target}?error=invalid_state&plugin={quote(key)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    except (OAuthFlowError, OAuthError, ProviderNotConfigured):
        return RedirectResponse(
            f"{target}?error=exchange_failed&plugin={quote(key)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    destination = (
        f"{settings.frontend_base_url.rstrip('/')}{redirect_to}"
        if redirect_to
        else target
    )

    joiner = "&" if "?" in destination else "?"

    return RedirectResponse(
        f"{destination}{joiner}connected={quote(key)}",
        # 303, not 302. The callback is a GET, but 303 states plainly
        # that the client must follow with a GET regardless of what it
        # thinks it was doing - which is what every OAuth callback
        # wants and what some clients get wrong on a bare 302.
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{key}/oauth/refresh", response_model=ConnectionRead)
async def oauth_refresh(
    key: str,
    current_user: CurrentUser,
    session: DbDep,
    settings: SettingsDep,
    store: CredentialStoreDep,
) -> ConnectionRead:
    """
    Force a token refresh now.

    Normal operation never calls this - tokens are refreshed before use
    (see oauth_service.access_token), which is the whole point of
    storing expires_at. It exists so a user can prove a broken
    connection is broken, and so an operator can reproduce a refresh
    failure without waiting an hour for one.
    """

    token = await oauth_service.access_token(
        session, settings, store, current_user.id, key
    )

    connection = await plugin_service.get_connection(
        session, current_user.id, key
    )

    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connection to {key!r}.",
        )

    if token is None:
        # The refresh failed and the connection has been marked
        # expired. 409, because the resource is in a state that cannot
        # serve the request - and the user's next step is to reconnect,
        # not to retry.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The {key!r} connection could not be refreshed. "
                "Reconnect the service."
            ),
        )

    return connection
