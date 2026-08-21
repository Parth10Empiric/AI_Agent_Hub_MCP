"""
Per-user credentials inside the MCP server (Phase 5.5).

THE PROBLEM THIS SOLVES

Until now every service was built ONCE, when the process started:

    github = GitHubService()          # services/github/tools.py
    self.token = os.getenv("GITHUB_TOKEN")

One process, one token, one GitHub account - so every Agent Hub user
was browsing whoever's token happened to be in .env. That is fine for a
personal tool and fatal for a product.

HOW IT IS FIXED, WITHOUT TOUCHING 61 TOOLS

The backend sends the caller's credentials in the MCP request METADATA:

    session.call_tool(name, args, meta={"credentials": {"github": "..."}})

A middleware reads that metadata and puts each token in a ContextVar
before the tool runs. The module-level `github` becomes a PROXY that
forwards every attribute to whichever service instance belongs to the
current request.

    tools.py                     unchanged - still calls `github.x()`
    the 4 error decorators       unchanged
    services/*/services.py       one optional `token=` parameter

WHY METADATA AND NOT A TOOL ARGUMENT

    1. tool arguments are visible to the LLM
    2. they land in ExecutionRecord.arguments
    3. every tool signature would have to change
    4. prompt injection could ask the model to echo one back

Request metadata is part of the MCP envelope. The model never sees it,
and it never reaches the database. That is the whole difference.

WHY ContextVar AND NOT A GLOBAL

A ContextVar is per-task. Two users' requests cannot see each other's
value even if the server later handles them concurrently - and, as the
tests assert, a request that sends no credentials does not inherit the
previous request's either. A module-level global would give both of
those away.
"""

from __future__ import annotations

import hashlib
import os
import time
from contextvars import ContextVar
from typing import Any, Callable

from core.logging import get_logger


logger = get_logger(__name__)


# The credentials for the request being handled right now, keyed by
# service namespace: {"github": "ghp_...", "slack": "xoxb-..."}.
#
# Empty by default, which is the safe direction: a request that sends
# nothing gets nothing, not whatever the last request happened to set.
_CREDENTIALS: ContextVar[dict[str, str]] = ContextVar(
    "mcp_credentials",
    default={},
)

# Who the request belongs to. Not used to authorise anything - the
# backend already did that - but it makes a log line answer "whose call
# was this?" without the token being anywhere near it.
_USER_ID: ContextVar[str | None] = ContextVar("mcp_user_id", default=None)


def current_user_id() -> str | None:
    return _USER_ID.get()


def credential_for(namespace: str) -> str | None:
    """The token this request carries for one service, if any."""

    return _CREDENTIALS.get().get(namespace)


# ---------------------------------------------------------------------
# The environment fallback
# ---------------------------------------------------------------------


def env_credentials_allowed() -> bool:
    """
    May a request with no credentials fall back to .env?

    YES in development. The CLI (ai_client.py), test_github.py and the
    whole local loop call the MCP server with no metadata at all, and
    breaking them would buy nothing.

    NO in production, and this is the important half. A user who has
    not connected GitHub must get an error - not silently inherit the
    operator's token, which is the exact bug this phase exists to
    remove, reintroduced through the back door.

    Read from the environment on every call rather than cached at
    import, so a test can flip it.
    """

    explicit = os.getenv("MCP_ALLOW_ENV_CREDENTIALS")

    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes"}

    environment = os.getenv("MCP_ENVIRONMENT", "development")

    return environment.strip().lower() != "production"


class MissingCredential(RuntimeError):
    """
    This request carries no credential for a service it needs, and the
    environment fallback is switched off.

    FAILING CLOSED IS THE POINT. The alternative - carrying on with
    whatever token the process was started with - is a cross-tenant
    leak that looks exactly like the feature working.
    """


# ---------------------------------------------------------------------
# The middleware
# ---------------------------------------------------------------------


class CredentialMiddleware:
    """
    Reads credentials off every inbound request.

    Registered on the MCPServer, so it wraps EVERY tool call - including
    the Drive tools, which have no decorator of their own. One place to
    read metadata, one place to audit, and nothing to remember when a
    new tool is added.

    It sets ContextVars and calls the rest of the chain. It never
    validates, never authorises and never refuses: by the time a call
    reaches here the backend has already checked the agent's scopes,
    the tool switch and the human approval. This is plumbing, not a
    gate - the gates are all upstream, in code the model cannot reach.
    """

    __slots__ = ()

    async def __call__(self, ctx: Any, call_next: Callable) -> Any:

        credentials, user_id = self._read(ctx)

        # SET UNCONDITIONALLY, AND RESET AFTERWARDS.
        #
        # The obvious version only sets the variable when credentials
        # are present, and relies on the SDK giving every request a
        # fresh context. It does - but relying on it means request N
        # inherits request N-1's token the day that changes, in a
        # transport nobody tested, silently.
        #
        # Setting on every request makes this middleware answer the
        # question by itself: whatever the surrounding context held,
        # a request sees exactly what it sent and nothing else. The
        # reset in the finally puts the previous value back, so this is
        # safe even when several calls DO share one context.
        #
        # A test caught this. It ran two calls in one task, which is
        # what the naive version cannot survive.
        cred_token = _CREDENTIALS.set(credentials)
        user_token = _USER_ID.set(user_id)

        try:
            return await call_next(ctx)

        finally:
            _CREDENTIALS.reset(cred_token)
            _USER_ID.reset(user_token)

    @staticmethod
    def _read(ctx: Any) -> tuple[dict[str, str], str | None]:
        """
        Pull credentials and user id out of the request metadata.

        Tolerant on purpose. Metadata arrives as a dict on stdio and
        may be a model elsewhere, and a malformed value must degrade to
        "no credentials" rather than raise - an exception here would
        fail a tool call for a reason that has nothing to do with it.
        """

        meta = getattr(ctx, "meta", None)

        if meta is None:
            return {}, None

        raw = meta if isinstance(meta, dict) else getattr(meta, "__dict__", {})

        if not isinstance(raw, dict):
            return {}, None

        credentials = raw.get("credentials")

        if not isinstance(credentials, dict):
            credentials = {}

        cleaned = {
            str(key): str(value)
            for key, value in credentials.items()
            if value
        }

        user_id = raw.get("user_id")

        return cleaned, str(user_id) if user_id else None


# ---------------------------------------------------------------------
# Per-token service instances
# ---------------------------------------------------------------------


# How long an idle service instance is kept, and how many at once.
#
# A service holds an HTTP client with a connection pool. Building one
# per CALL would throw away every keep-alive connection; keeping one
# per user forever would leak a pool per user who ever logged in.
CACHE_TTL_SECONDS = 900
CACHE_MAX_ENTRIES = 32


_cache: dict[tuple[str, str], tuple[Any, float]] = {}


def _fingerprint(token: str | None) -> str:
    """
    A short hash of the token, used as the cache key.

    NOT the token itself. A dict keyed by live credentials shows every
    one of them in a debugger, a heap dump or a stray repr of the
    cache. The hash identifies without exposing.
    """

    if token is None:
        return "env"

    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _evict(now: float) -> None:
    """Drop expired entries, then the oldest if still over the cap."""

    for key, (service, touched) in list(_cache.items()):
        if now - touched > CACHE_TTL_SECONDS:
            _close(_cache.pop(key, (None, 0))[0])

    while len(_cache) > CACHE_MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][1])
        _close(_cache.pop(oldest, (None, 0))[0])


def _close(service: Any) -> None:
    """
    Best effort. A service that cannot be closed is not worth an error.

    GitHub's close() is a coroutine and Slack's is not, so the async
    one is only awaited when a loop is actually running - and if it is
    not, the connection is left to the garbage collector rather than
    raising inside an eviction.
    """

    closer = getattr(service, "close", None)

    if closer is None:
        return

    try:
        result = closer()

        if hasattr(result, "__await__"):
            import asyncio

            try:
                asyncio.get_running_loop().create_task(result)

            except RuntimeError:
                result.close()

    except Exception:
        logger.debug("could not close a cached service", exc_info=True)


def _resolve(namespace: str, factory: Callable[[str | None], Any]) -> Any:
    """
    The service instance for THIS request's credentials.

    Cached by (service, token fingerprint) so a user's second tool call
    in a turn reuses the same HTTP connection pool as the first.
    """

    token = credential_for(namespace)

    if token is None and not env_credentials_allowed():
        raise MissingCredential(
            f"No {namespace} credentials for this request. "
            f"Connect {namespace} in Agent Hub and try again."
        )

    key = (namespace, _fingerprint(token))

    now = time.monotonic()

    cached = _cache.get(key)

    if cached is not None:
        service, _ = cached
        _cache[key] = (service, now)
        return service

    service = factory(token)

    _cache[key] = (service, now)

    _evict(now)

    return service


class ServiceProxy:
    """
    Looks like a service, resolves to the caller's one.

    Every attribute access forwards to whichever instance belongs to
    the current request:

        github.list_repositories(...)
          -> _resolve("github", ...).list_repositories(...)

    Which is why sixty-one tool bodies needed no edit at all. They
    still reference a module-level name; that name simply stopped being
    a single shared object.
    """

    __slots__ = ("_namespace", "_factory")

    def __init__(
        self,
        namespace: str,
        factory: Callable[[str | None], Any],
    ) -> None:
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_factory", factory)

    def __getattr__(self, name: str) -> Any:
        return getattr(
            _resolve(
                object.__getattribute__(self, "_namespace"),
                object.__getattribute__(self, "_factory"),
            ),
            name,
        )

    def __repr__(self) -> str:
        # Never renders the service - and therefore never a token - in
        # a traceback or a log line.
        return f"<ServiceProxy {object.__getattribute__(self, '_namespace')}>"


def service_proxy(
    namespace: str,
    factory: Callable[[str | None], Any],
) -> ServiceProxy:
    """Replace a module-level singleton with one of these."""

    return ServiceProxy(namespace, factory)


def reset_cache() -> None:
    """Drop every cached instance. For tests."""

    for service, _ in list(_cache.values()):
        _close(service)

    _cache.clear()
