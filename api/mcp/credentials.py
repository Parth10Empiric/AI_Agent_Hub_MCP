from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine

from api.credentials import CredentialError, CredentialStore
from api.services import oauth_service
from api.settings import APISettings
from core.logging import get_logger


logger = get_logger(__name__)


"""
Resolving one user's credentials for one tool call (Phase 5.5).

WHERE THIS SITS

    chat_service      builds a resolver for the user whose turn it is
        |
    ScopedSession     calls it once per tool call, puts the answer in
        |             the MCP request metadata
    MCP server        core/tenancy reads the metadata and hands the
                      token to the service

WHY THE BACKEND RESOLVES, AND NOT THE MCP SERVER

Phase5.md sketches a resolver INSIDE the MCP server: send the user_id,
let the server read the database and decrypt. That works, and it is the
right answer the day the MCP server becomes a separate deployable. It
is the wrong answer today, for three reasons:

  1. The refresh logic already lives here. oauth_service.access_token
     refreshes before expiry under a row lock. Putting a resolver in
     the MCP server means writing all of that again - or using tokens
     it cannot refresh.

  2. The MCP server would need DATABASE_URL and the Fernet key. The
     project deliberately keeps it ignorant of backend secrets;
     api/settings.py says so in as many words.

  3. The document's four objections to passing a token are all about
     TOOL ARGUMENTS - visible to the model, stored in
     ExecutionRecord.arguments, changing every signature, echoable by
     prompt injection. None of them apply to request metadata, which
     the model never sees and which is never persisted.

The token crosses a stdio pipe between a parent process and its own
child on the same machine. Not a network.
"""


# Which service a tool belongs to comes from the tool itself
# (ToolDefinition.namespace, set by Phase 2 discovery), never from
# parsing the name. A prefix check would break the first time a tool is
# renamed, and would break silently.


class CredentialResolver:
    """
    Answers "what does this user need, to make this call?"

    ONE service per call, not all four. A user asking for GitHub issues
    should not cause their Drive and Slack credentials to be decrypted,
    refreshed and shipped across a pipe - each of those is an
    opportunity to leak something the call never needed.
    """

    __slots__ = ("_session", "_engine", "_settings", "_store", "_user_id")

    def __init__(
        self,
        session: AsyncSession,
        engine: AgentEngine,
        settings: APISettings,
        store: CredentialStore,
        user_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._engine = engine
        self._settings = settings
        self._store = store
        self._user_id = user_id

    async def meta_for(self, tool_name: str) -> dict[str, Any]:
        """
        The MCP request metadata for one tool call.

        Returns at minimum the user id, so a log line on the server can
        say whose call it was without the token being anywhere near it.
        """

        meta: dict[str, Any] = {"user_id": str(self._user_id)}

        definition = self._engine.get_tool(tool_name)

        namespace = definition.namespace if definition else None

        if not namespace:
            # An unknown tool. The executor refuses it a moment later
            # anyway; sending no credentials means that refusal happens
            # with nothing attached.
            return meta

        token = await self._token_for(namespace)

        if token:
            meta["credentials"] = {namespace: token}

        # No token: the metadata simply has no credentials key. What
        # happens next is the MCP server's decision, and it depends on
        # MCP_ALLOW_ENV_CREDENTIALS - .env in development, a refusal in
        # production. Failing closed there rather than here keeps the
        # rule in ONE place.

        return meta

    async def _token_for(self, namespace: str) -> str | None:

        try:
            return await oauth_service.access_token(
                self._session,
                self._settings,
                self._store,
                self._user_id,
                namespace,
            )

        except CredentialError:
            # The stored blob cannot be decrypted - almost always a key
            # that was rotated away. Logged here, and treated as "not
            # connected" so the tool call fails with a message the user
            # can act on instead of a 500.
            logger.warning(
                "could not decrypt %s credentials for user %s",
                namespace,
                self._user_id,
            )
            return None

        except Exception:
            logger.exception(
                "credential resolution failed for %s", namespace
            )
            return None


def build_resolver(
    session: AsyncSession,
    engine: AgentEngine,
    settings: APISettings | None,
    store: CredentialStore | None,
    user_id: uuid.UUID,
) -> CredentialResolver | None:
    """
    A resolver, or None when the caller cannot provide one.

    None means "send no credentials", which is exactly what the CLI and
    the tests want - and what makes this change invisible to them.
    """

    if settings is None or store is None:
        return None

    return CredentialResolver(session, engine, settings, store, user_id)
