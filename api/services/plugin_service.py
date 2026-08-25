from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine
from agent.schemas import ToolDefinition

from api.credentials import CredentialStore
from api.db.models import PluginConnection
from api.audit import AuditAction, ResourceType
from api.oauth import build_provider, supports_oauth
from api.settings import APISettings
from api.verification import (
    CredentialRejected,
    VerifiedCredential,
    verify_credential,
)
from api.plugin_meta import presentation_for
from api.services import audit_service
from api.schemas.plugin import (
    ConnectionCheck,
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
    verifier: Callable[..., Awaitable[VerifiedCredential]] | None = None,
) -> ConnectionRead:
    """
    Prove a credential works, then store it encrypted.

    Connecting an already-connected service REPLACES the credential
    rather than failing. That is what a user means by "reconnect", and
    the unique constraint on (user_id, plugin_key) means there is only
    ever one row to update.

    VERIFICATION COMES FIRST, AND NOTHING IS WRITTEN WITHOUT IT

    This function used to encrypt whatever string arrived, set status
    "connected" and return 201. Paste "hello-world-1234" and the UI
    showed a green badge; the failure surfaced later, inside an agent
    turn, as an error nobody could trace back to the box they typed in.

    "Connected" has to mean a connection was made. The only thing that
    can establish that is the service itself, so we ask it - see
    api/verification.py - and a rejected credential never reaches the
    database at all. There is no half state to clean up, no row to
    explain, and the error appears in the dialog where the mistake was
    made.

    THE LABEL AND SCOPES NOW COME FROM THE SERVICE

    They used to be taken from the request body, so both were claims by
    whoever pasted the token rather than facts about the account it
    opens - and a connection could sit in the list labelled
    "finance@company.com" while holding a personal token. The client's
    label survives only as a fallback for services that report none.

    `verifier` is injectable so tests do not need the network. The
    default is the real one; a caller that passes None gets checked for
    real, which is the safe direction for a parameter to default in.
    """

    if not engine.registry.by_namespace(key):
        raise UnknownPlugin(key)

    check = verifier or verify_credential

    # BEFORE the encrypt, before the row, before the audit line.
    # Raises CredentialRejected or VerificationUnavailable, both of
    # which the router turns into a specific HTTP status - see
    # api/routers/plugins.py.
    verified = await check(key, credential)

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

    # What the SERVICE said this credential is, with the client's
    # label kept only as a fallback for a service that reports none.
    connection.account_label = verified.account_label or account_label
    connection.scopes = list(verified.scopes) or list(scopes or [])
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
        # The VERIFIED account, so the audit trail records whose
        # credential this actually is rather than what the person
        # connecting chose to call it.
        account=connection.account_label,
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


async def verify_connection(
    session: AsyncSession,
    store: CredentialStore,
    user_id: uuid.UUID,
    key: str,
    *,
    verifier: Callable[..., Awaitable[VerifiedCredential]] | None = None,
) -> ConnectionCheck | None:
    """
    Re-check a stored credential against the service. None if not connected.

    WHY THIS EXISTS SEPARATELY FROM `connect`

    Verifying at connect time proves a credential worked ONCE. Tokens
    are then revoked on the provider's website, expire, get rotated by
    a security policy, or belong to an account that loses access to a
    repository - and none of that sends us a notification. A
    connection is a claim with a shelf life, and the only way to know
    it still holds is to ask again.

    It also settles the connections that were stored BEFORE any of this
    existed, which were never checked at all.

    WHY IT RETURNS A RESULT INSTEAD OF RAISING ON A BAD TOKEN

    Because it writes. A rejected check sets `status = "revoked"`, and
    api/db/session.get_db rolls the transaction back whenever an
    endpoint raises - so raising would report the bad token correctly
    and then discard the very row that recorded it. The user would see
    the warning and the database would forget it.

    "The check ran and the answer was no" is a successful request with
    a negative result, not a failed request. Only the third outcome -
    we could not reach the service - raises, because then nothing was
    learned and nothing should change.
    """

    connection = await _connection(session, user_id, key)

    if connection is None:
        return None

    payload = store.decrypt(connection.credentials_enc)

    # Both shapes. A pasted token is stored under "credential"; an
    # OAuth token set under "access_token" - and TokenSet.to_payload
    # writes both, so this order is belt and braces rather than a
    # branch that can go stale.
    credential = payload.get("credential") or payload.get("access_token")

    if not credential:
        connection.status = "revoked"
        await session.flush()

        return ConnectionCheck(
            valid=False,
            detail=(
                "The stored credential could not be read. Reconnect "
                "this service."
            ),
            connection=ConnectionRead.model_validate(connection),
        )

    check = verifier or verify_credential

    try:
        verified = await check(key, credential)

    except CredentialRejected as exc:

        connection.status = "revoked"

        audit_service.record(
            session,
            AuditAction.PLUGIN_CHECK_FAILED,
            user_id=user_id,
            actor_user_id=user_id,
            resource_type=ResourceType.PLUGIN,
            plugin=key,
            account=connection.account_label,
            reason=str(exc),
        )

        await session.flush()

        return ConnectionCheck(
            valid=False,
            detail=str(exc),
            connection=ConnectionRead.model_validate(connection),
        )

    # Still good - and possibly better than we knew. A connection that
    # was marked revoked by an earlier check recovers here without the
    # user having to reconnect, which is what happens when a service
    # was simply having a bad day.
    connection.status = "connected"

    if verified.account_label:
        connection.account_label = verified.account_label

    if verified.scopes:
        connection.scopes = list(verified.scopes)

    await session.flush()

    return ConnectionCheck(
        valid=True,
        detail=f"{key} accepted this credential.",
        connection=ConnectionRead.model_validate(connection),
    )


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
