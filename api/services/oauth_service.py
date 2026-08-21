from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import AuditAction, ResourceType
from api.credentials import CredentialStore
from api.db.models import OAuthState, PluginConnection
from api.oauth import (
    OAuthError,
    ProviderNotConfigured,
    build_provider,
    new_state,
    new_verifier,
)
from api.oauth.base import TokenSet
from api.services import audit_service
from api.settings import APISettings
from core.logging import get_logger


logger = get_logger(__name__)


"""
The OAuth flow, everything except the provider-specific parts.

Read this file as two halves that never meet in one request:

    start()      an authenticated XHR from our own frontend
    complete()   an unauthenticated redirect from the provider

The second one is the interesting half. It arrives as a BROWSER
navigation, so it carries no Authorization header and cannot use
CurrentUser - the OAuthState row is the only thing that knows who
started the flow.
"""


class OAuthFlowError(Exception):
    """The callback could not be trusted, or the exchange failed."""


class InvalidState(OAuthFlowError):
    """
    The `state` was missing, unknown, expired, already used, or minted
    for a different service.

    ONE exception for all five, and one message to the caller. Telling
    an attacker which of those went wrong is a free oracle: "expired"
    confirms the value was real, "unknown" confirms it was not.
    """


# ---------------------------------------------------------------------
# start
# ---------------------------------------------------------------------


async def start(
    session: AsyncSession,
    settings: APISettings,
    user_id: uuid.UUID,
    key: str,
    *,
    redirect_to: str | None = None,
) -> str:
    """
    Mint a state row and return the provider's consent URL.

    RETURNS A URL RATHER THAN A 302, deliberately.

    This endpoint is bearer-authenticated, and a browser NAVIGATION
    cannot send an Authorization header. If it answered with a
    redirect, the frontend would have to navigate to it directly - and
    then it could not authenticate. So the frontend fetches the URL
    with a normal authenticated request and navigates to the result.

    That inverts the usual sketch and removes the need for cookie auth
    on this one route, which would otherwise be a second authentication
    mechanism to maintain and get wrong.
    """

    provider = build_provider(settings, key)

    verifier = new_verifier() if provider.uses_pkce else None

    state = new_state()

    session.add(
        OAuthState(
            state=state,
            user_id=user_id,
            plugin_key=key,
            code_verifier=verifier,
            redirect_to=_safe_redirect(settings, redirect_to),
            expires_at=(
                datetime.now(timezone.utc)
                + timedelta(seconds=settings.oauth_state_ttl_seconds)
            ),
        )
    )

    await session.flush()

    return provider.authorize_url(state=state, verifier=verifier)


def _safe_redirect(settings: APISettings, value: str | None) -> str | None:
    """
    Only ever a path within our own frontend.

    An unchecked `redirect_to` is an open redirect: an attacker sends a
    victim a link that starts a genuine OAuth flow on YOUR domain and
    lands them on a page that is not yours. The address bar says your
    app the whole way.

    Rejecting anything that is not a relative path makes that
    impossible, and costs one line.
    """

    if not value:
        return None

    if not value.startswith("/") or value.startswith("//"):
        return None

    return value[:512]


# ---------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------


async def complete(
    session: AsyncSession,
    settings: APISettings,
    store: CredentialStore,
    key: str,
    *,
    state: str | None,
    code: str | None,
) -> tuple[PluginConnection, str | None]:
    """
    Verify the callback, exchange the code, store the connection.

    THE ORDER IS THE SECURITY:

        1. the state exists, is unexpired, unused, and for THIS plugin
        2. mark it used, before anything else can fail
        3. exchange the code
        4. encrypt and store

    Step 2 goes before step 3 on purpose. If the exchange failed and
    the state stayed usable, a replayed callback would get another
    attempt - which is exactly the window a stolen code needs.
    """

    if not state or not code:
        raise InvalidState("Missing state or code.")

    row = await session.scalar(
        select(OAuthState).where(OAuthState.state == state)
    )

    now = datetime.now(timezone.utc)

    if (
        row is None
        or row.used_at is not None
        or row.expires_at <= now
        # A state minted to connect Slack must not be redeemable on the
        # GitHub callback. Without this the user_id is still right, but
        # the ATTACKER chooses which of their accounts gets attached.
        or row.plugin_key != key
    ):
        raise InvalidState("This authorisation request is no longer valid.")

    row.used_at = now
    await session.flush()

    provider = build_provider(settings, key)

    try:
        tokens = await provider.exchange(
            code=code,
            verifier=row.code_verifier,
        )

    except OAuthError as exc:
        logger.warning("oauth exchange failed for %s: %s", key, exc)
        raise OAuthFlowError(str(exc)) from None

    label = await _safe_identity(provider, tokens.access_token)

    connection = await _store(
        session, store, row.user_id, key, tokens, label
    )

    return connection, row.redirect_to


async def _safe_identity(provider, access_token: str) -> str | None:
    """
    Ask the provider who this token belongs to.

    Best effort. A failure here means the label is missing from the UI,
    which is cosmetic; failing the whole connection over it would throw
    away a token the user has already consented to.
    """

    try:
        return await provider.identity(access_token)

    except Exception:
        logger.warning("could not read identity for %s", provider.key)
        return None


async def _store(
    session: AsyncSession,
    store: CredentialStore,
    user_id: uuid.UUID,
    key: str,
    tokens: TokenSet,
    label: str | None,
) -> PluginConnection:
    """
    Write the encrypted token set to plugin_connections.

    Reconnecting REPLACES the row's credential rather than adding a
    second row - the unique constraint on (user_id, plugin_key) means
    there is only ever one, and "reconnect" is what a user means when
    they run the flow again.

    The plaintext exists only in the local `tokens` variable. What
    reaches the database is ciphertext.
    """

    connection = await session.scalar(
        select(PluginConnection).where(
            PluginConnection.user_id == user_id,
            PluginConnection.plugin_key == key,
        )
    )

    if connection is None:
        connection = PluginConnection(user_id=user_id, plugin_key=key)
        session.add(connection)

    connection.credentials_enc = store.encrypt(tokens.to_payload())

    # WHICH key wrote these bytes. Not a constant: it comes from the
    # store, so adding a new key at the front of the list makes every
    # row written afterwards current and every earlier row visibly
    # stale - which is exactly what the rotation job selects on.
    connection.key_version = store.version
    connection.status = "connected"
    connection.connected_at = datetime.now(timezone.utc)
    connection.expires_at = tokens.expires_at

    # What the provider ACTUALLY granted, which can be less than what
    # was asked for. "The user declined Drive write" must be a visible
    # state, not a mysterious 403 three screens later.
    connection.scopes = list(tokens.scopes)

    if label:
        connection.account_label = label

    audit_service.record(
        session,
        AuditAction.PLUGIN_CONNECTED,
        user_id=user_id,
        actor_user_id=user_id,
        resource_type=ResourceType.PLUGIN,
        plugin=key,
        method="oauth",
        account=label,
        # The scopes GRANTED, which can be fewer than were asked for.
        # This is the row that answers "what was this token ever
        # allowed to do?" long after the token is gone.
        scopes=list(tokens.scopes) or None,
    )

    await session.flush()

    return connection


# ---------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------


async def access_token(
    session: AsyncSession,
    settings: APISettings,
    store: CredentialStore,
    user_id: uuid.UUID,
    key: str,
) -> str | None:
    """
    A usable access token, refreshed if it is about to expire.

    REFRESH BEFORE EXPIRY, NOT AFTER.

        bad    call -> 401 -> refresh -> retry
               (a user-visible failure every hour)

        good   before use: expires_at < now + skew -> refresh
               (invisible)

    The skew has to be larger than the longest request you expect. A
    token that passes the check with two seconds left and then expires
    mid-request lands you on the bad path anyway.
    """

    connection = await _locked_connection(session, user_id, key)

    if connection is None:
        return None

    payload = store.decrypt(connection.credentials_enc)

    token = payload.get("access_token") or payload.get("credential")

    if not _needs_refresh(connection, settings):
        connection.last_used_at = datetime.now(timezone.utc)
        return token

    refresh_token = payload.get("refresh_token")

    if not refresh_token:
        # Nothing to refresh with. GitHub and Slack land here by
        # design - their tokens do not expire - so this is only a
        # problem if expires_at was set, which it is not for them.
        return token

    try:
        provider = build_provider(settings, key)
        tokens = await provider.refresh(refresh_token)

    except (OAuthError, ProviderNotConfigured) as exc:
        # ONLY a failed REFRESH marks the connection expired. An
        # expired access token is normal and invisible; an expired
        # REFRESH token means the user has to reconnect, and saying so
        # plainly beats an integration that quietly returns nothing.
        logger.warning("refresh failed for %s/%s: %s", user_id, key, exc)

        connection.status = "expired"
        await session.flush()

        # Observational, and in the caller's session anyway because the
        # status change beside it must be durable either way. A refresh
        # that failed is how a working integration becomes a broken
        # one, and it is worth being able to see when it happened.
        session.add(
            audit_service.entry(
                AuditAction.PLUGIN_REFRESH_FAILED,
                user_id=user_id,
                resource_type=ResourceType.PLUGIN,
                plugin=key,
                reason=type(exc).__name__,
            )
        )

        return None

    connection.credentials_enc = store.encrypt(tokens.to_payload())
    connection.key_version = store.version
    connection.expires_at = tokens.expires_at
    connection.status = "connected"
    connection.last_used_at = datetime.now(timezone.utc)

    if tokens.scopes:
        connection.scopes = list(tokens.scopes)

    await session.flush()

    return tokens.access_token


async def _locked_connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
) -> PluginConnection | None:
    """
    Load the connection FOR UPDATE.

    Two turns refreshing the same connection at the same moment would
    both POST to the token endpoint. Google invalidates the old refresh
    token when it issues a new one, so the slower writer would store a
    token that is already dead - and the connection would break at the
    NEXT refresh, minutes later, far from the cause.

    The row lock makes the second caller wait, re-read, and discover
    the token is already fresh.
    """

    return await session.scalar(
        select(PluginConnection)
        .where(
            PluginConnection.user_id == user_id,
            PluginConnection.plugin_key == key,
        )
        .with_for_update()
    )


def _needs_refresh(
    connection: PluginConnection,
    settings: APISettings,
) -> bool:

    if connection.expires_at is None:
        # No expiry recorded: a GitHub or Slack token, or a pasted
        # personal access token. Refreshing it is not possible and not
        # needed.
        return False

    deadline = datetime.now(timezone.utc) + timedelta(
        seconds=settings.oauth_refresh_skew_seconds
    )

    return connection.expires_at <= deadline


async def purge_expired_states(
    session: AsyncSession,
    *,
    before: datetime | None = None,
) -> int:
    """
    Delete authorisation attempts nobody finished.

    A user who opens a consent screen and closes the tab leaves a row
    behind. Harmless individually - it is unusable the moment it
    expires - but nothing else ever removes it.
    """

    cutoff = before or datetime.now(timezone.utc)

    result = await session.execute(
        delete(OAuthState).where(OAuthState.expires_at <= cutoff)
    )

    return result.rowcount or 0
