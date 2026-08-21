from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine
from agent.schemas import ToolDefinition

from api.credentials import CredentialStore
from api.db.models import PluginConnection
from api.audit import AuditAction, ResourceType
from api.oauth import build_provider, supports_oauth
from api.settings import APISettings
from api.plugin_meta import presentation_for
from api.services import audit_service
from api.schemas.plugin import (
    ConnectionRead,
    PluginDetail,
    PluginSummary,
    ToolSummary,
)


class PluginError(Exception):
    """Base for plugin failures."""


class UnknownPlugin(PluginError):
    """
    No service by that key exists in the live registry.

    Note what "exists" means: not "is in a list we maintain", but "the
    MCP server is currently exposing tools for it". A service that is
    removed from the MCP server stops being connectable immediately,
    with no code change.
    """


# ---------------------------------------------------------------------
# Catalogue - derived from the live registry
# ---------------------------------------------------------------------


def _tool_summary(tool: ToolDefinition) -> ToolSummary:
    return ToolSummary(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        operation=tool.operation.value,
        risk_level=tool.risk_level.value,
        read_only=tool.read_only,
        requires_approval=tool.requires_approval,
        permissions=list(tool.permissions),
    )


def _summary(
    key: str,
    tools: list[ToolDefinition],
    connection: PluginConnection | None,
    settings: APISettings | None = None,
) -> PluginSummary:

    meta = presentation_for(key)

    # Optional so every existing caller keeps working. When settings
    # are not passed the answer is "no OAuth", which is the safe way
    # round: the UI falls back to the token dialog rather than offering
    # a flow that may not be configured.
    oauth_available = bool(settings) and supports_oauth(settings, key)

    scopes: list[str] = []

    if oauth_available and settings is not None:
        scopes = list(build_provider(settings, key).scopes)

    return PluginSummary(
        key=key,
        label=meta.label,
        description=meta.description,
        icon=meta.icon,
        category=meta.category,
        auth_type=meta.auth_type,
        docs_url=meta.docs_url,
        tool_count=len(tools),
        operations=dict(
            sorted(Counter(t.operation.value for t in tools).items())
        ),
        connected=connection is not None,
        account_label=connection.account_label if connection else None,
        status=connection.status if connection else None,
        oauth_available=oauth_available,
        oauth_scopes=scopes,
    )


async def list_catalogue(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    settings: APISettings | None = None,
) -> list[PluginSummary]:
    """
    Every service the MCP server currently exposes, marked with whether
    this user has connected it.

    ONE query for the connections, then an in-memory join - not a
    lookup per service. With four services the difference is invisible;
    the habit is what matters, and it is the same habit that keeps the
    conversations page fast.
    """

    connections = {
        c.plugin_key: c
        for c in await _user_connections(session, user_id)
    }

    return [
        _summary(
            key,
            engine.registry.by_namespace(key),
            connections.get(key),
            settings,
        )
        for key in engine.registry.servers()
    ]


async def get_plugin(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    key: str,
    settings: APISettings | None = None,
) -> PluginDetail:

    tools = engine.registry.by_namespace(key)

    # The registry is the authority on what exists. An unknown key is
    # a 404 whether it was never real or was just removed from the MCP
    # server.
    if not tools:
        raise UnknownPlugin(key)

    connection = await _connection(session, user_id, key)

    base = _summary(key, tools, connection, settings)

    return PluginDetail(
        **base.model_dump(),
        tools=[
            _tool_summary(t)
            for t in sorted(tools, key=lambda t: t.name)
        ],
    )


# ---------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------


async def _user_connections(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[PluginConnection]:
    return list(
        await session.scalars(
            select(PluginConnection)
            .where(PluginConnection.user_id == user_id)
            .order_by(PluginConnection.plugin_key)
        )
    )


async def _connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
) -> PluginConnection | None:
    """
    Always filtered by user_id, never by plugin_key alone.

    This is where IDOR would live if it were going to. A query that
    finds a connection by key without checking who owns it returns
    somebody else's account.
    """

    return await session.scalar(
        select(PluginConnection).where(
            PluginConnection.user_id == user_id,
            PluginConnection.plugin_key == key,
        )
    )


async def list_connections(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[ConnectionRead]:
    return [
        ConnectionRead.model_validate(c)
        for c in await _user_connections(session, user_id)
    ]


async def connect(
    session: AsyncSession,
    engine: AgentEngine,
    store: CredentialStore,
    user_id: uuid.UUID,
    key: str,
    *,
    credential: str,
    account_label: str | None = None,
    scopes: list[str] | None = None,
) -> ConnectionRead:
    """
    Store an encrypted credential for one service.

    Connecting an already-connected service REPLACES the credential
    rather than failing. That is what a user means by "reconnect", and
    the unique constraint on (user_id, plugin_key) means there is only
    ever one row to update.
    """

    if not engine.registry.by_namespace(key):
        raise UnknownPlugin(key)

    # The plaintext credential exists only in this local variable and
    # is never assigned to the model. What reaches the database is
    # ciphertext.
    blob = store.encrypt({"credential": credential})

    now = datetime.now(timezone.utc)

    connection = await _connection(session, user_id, key)

    if connection is None:
        connection = PluginConnection(
            user_id=user_id,
            plugin_key=key,
        )
        session.add(connection)

    connection.credentials_enc = blob

    # Stamped from the store, never hardcoded. A row that says
    # key_version = 1 while the store is on version 2 is a row the
    # rotation job still has to visit - and that comparison is the
    # whole mechanism.
    connection.key_version = store.version

    connection.account_label = account_label
    connection.scopes = list(scopes or [])
    connection.status = "connected"
    connection.connected_at = now
    connection.expires_at = None

    # Transactional: connecting a service IS the state change, and a
    # connection whose audit row is missing is a credential nobody can
    # account for.
    #
    # The KEY, never the credential. This table is append-only and kept
    # for a year.
    audit_service.record(
        session,
        AuditAction.PLUGIN_CONNECTED,
        user_id=user_id,
        actor_user_id=user_id,
        resource_type=ResourceType.PLUGIN,
        plugin=key,
        method="token",
        account=account_label,
    )

    await session.flush()

    return ConnectionRead.model_validate(connection)


async def disconnect(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
) -> bool:
    """
    Remove a connection. Returns False if there was nothing to remove.

    A real DELETE, not a status change. There is no audit value in
    keeping an encrypted credential the user asked to remove, and every
    day it stays is another day it could leak.
    """

    connection = await _connection(session, user_id, key)

    if connection is None:
        return False

    await session.delete(connection)

    # The credential row is gone; the record that it existed is not.
    # "When did this account stop being connected?" has to remain
    # answerable after the thing it asks about was deleted.
    audit_service.record(
        session,
        AuditAction.PLUGIN_DISCONNECTED,
        user_id=user_id,
        actor_user_id=user_id,
        resource_type=ResourceType.PLUGIN,
        plugin=key,
    )

    return True


async def get_credential(
    session: AsyncSession,
    store: CredentialStore,
    user_id: uuid.UUID,
    key: str,
) -> str | None:
    """
    Decrypt a stored credential for internal use.

    Never exposed through an endpoint. This exists for Phase 5, where
    the MCP server resolves per-user credentials, and it touches
    last_used_at so a user can see which integrations are actually
    being exercised.
    """

    connection = await _connection(session, user_id, key)

    if connection is None:
        return None

    payload = store.decrypt(connection.credentials_enc)

    connection.last_used_at = datetime.now(timezone.utc)

    return payload.get("credential")


async def get_connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
) -> ConnectionRead | None:
    """
    One connection, as the API describes it. Never the credential.

    ConnectionRead has no field for credentials_enc, so there is no
    path from this function to a token in an HTTP response - the shape
    of the response model is the guarantee, not a rule someone has to
    remember.
    """

    connection = await _connection(session, user_id, key)

    if connection is None:
        return None

    return ConnectionRead.model_validate(connection)
