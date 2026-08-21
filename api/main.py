from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from core.logging import get_logger, setup_logging

from agent.engine import AgentEngine

from api.credentials import build_credential_store
from api.db.session import build_engine, build_sessionmaker
from api.mcp.provider import SharedSessionProvider
from api.routers import (
    agents,
    approvals,
    audit,
    auth,
    chat,
    conversations,
    executions,
    health,
    limits,
    permissions,
    plugins,
    stream,
)
from api.errors import install_error_handlers
from api.middleware import (
    ErrorEnvelopeMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from api.notifier import build_approval_notifier
from api.ratelimit import build_rate_limiter
from api.settings import APISettings, get_settings

# Imported for its SIDE EFFECT: importing the models registers them on
# Base.metadata, which is what Alembic autogenerate inspects. Without
# this import the metadata is empty and migrations come out wrong.
import api.db.models  # noqa: F401


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup and shutdown, once per process.

    Everything before `yield` runs when the server boots. Everything
    after runs when it stops. The application serves requests during
    the yield.

    This is where expensive, long-lived things belong. The AgentEngine
    spawns the MCP subprocess and classifies 61 tools - seconds of work
    that must happen ONCE, not per request. Phase 3 steps 4 and 5 add
    it here.
    """

    settings: APISettings = app.state.settings

    logger.info(
        "API starting: %s v%s (%s)",
        settings.api_title,
        settings.api_version,
        settings.api_environment,
    )

    # The connection pool is opened ONCE, here, and shared by every
    # request. Creating it per request would mean a TCP handshake and
    # an authentication round trip on every call.
    engine = build_engine(settings)

    app.state.db_engine = engine
    app.state.sessionmaker = build_sessionmaker(engine)

    logger.info("database pool ready")

    # The MCP subprocess and the tool registry are built ONCE.
    #
    # Discovery spawns a subprocess, performs the MCP handshake, lists
    # 61 tools and classifies every one of them. Seconds of work. Doing
    # it per request is not slow, it is impossible.
    provider = SharedSessionProvider()
    await provider.start()

    agent_engine = AgentEngine(server_name="personal-mcp-server")

    async with provider.session() as mcp_session:
        count = await agent_engine.discover_tools(mcp_session)

    app.state.mcp_provider = provider
    app.state.agent_engine = agent_engine

    # Cheap to build, but built once so a bad key fails at STARTUP
    # rather than on the first connect attempt.
    app.state.credential_store = build_credential_store(settings)

    # The approval wakeup registry (Phase 5.2). One per PROCESS, and
    # that is also its limit: the events live in this worker's memory,
    # so a suspended turn can only be resolved by a request that lands
    # on this same worker. Run one worker until Redis arrives in
    # Phase 6. See api/notifier.py.
    app.state.approval_notifier = build_approval_notifier()

    # PHASE 5.7. One per PROCESS, and that is also its limit: the
    # windows live in this worker's memory, so with four workers the
    # effective limit is four times what it says.
    #
    # This project already runs ONE worker because the approval
    # notifier requires it - so today the count is exact. The two
    # constraints must be lifted together, when Redis arrives.
    app.state.rate_limiter = build_rate_limiter()

    logger.info(
        "MCP ready: %d tools across %d services",
        count,
        len(agent_engine.registry.servers()),
    )

    yield

    # Shutdown runs in REVERSE order of startup: the MCP subprocess is
    # terminated after the app has stopped accepting requests, so no
    # request is left holding a session that has just been closed.
    logger.info("API shutting down")

    # Reverse order of startup: the MCP subprocess is terminated after
    # the app has stopped accepting requests, so no in-flight request
    # is left holding a session that just closed.
    await provider.stop()

    # Closes every pooled connection. Without this the process can hang
    # on exit waiting for asyncpg's background tasks, and PostgreSQL is
    # left holding sockets until it times them out.
    await engine.dispose()


def create_app(settings: APISettings | None = None) -> FastAPI:
    """
    Build the application.

    A FACTORY, not a module-level `app = FastAPI()`, because tests need
    to build an app pointed at a test database - and a module-level app
    is constructed the instant anything imports this file, before a
    test can configure anything.

    The `settings` parameter is what makes that possible.
    """

    settings = settings or get_settings()

    setup_logging()

    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        lifespan=lifespan,

        # /docs is an interactive page that can CALL your API. Useful
        # while building, not something to expose publicly.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
    )

    # State goes on the app instance, NEVER in a module-level global.
    #
    # A global would be shared by every app any test creates, producing
    # the worst kind of test: one that passes alone and fails in the
    # suite. app.state belongs to THIS app.
    app.state.settings = settings

    # The FIRST middleware on this app.
    #
    # A middleware rather than a dependency because a dependency runs
    # per endpoint, and the endpoint that forgets to ask for it is
    # unlimited. This wraps everything, including endpoints written
    # next year.
    #
    # Built here rather than in the lifespan because middleware must be
    # added before the app starts serving - so it gets its own limiter,
    # and the lifespan one is what the request path uses. Sharing one
    # would mean the middleware could not exist until startup finished.
    app.state.http_rate_limiter = build_rate_limiter()

    # ADDED FIRST, RUNS INNERMOST.
    #
    # An unhandled exception must become a response BEFORE it reaches
    # the middleware above, or the 500 is produced by Starlette outside
    # the whole stack - with no security headers and no request id, on
    # the one response most likely to be poked at.
    app.add_middleware(ErrorEnvelopeMiddleware)

    app.add_middleware(
        RateLimitMiddleware,
        limiter=app.state.http_rate_limiter,
        settings=settings,
    )

    # ADDED LAST, RUNS FIRST.
    #
    # Starlette wraps middleware in reverse order of registration, so
    # the last one added is the outermost. That is what this needs: a
    # request id must exist before anything else runs, including the
    # rate limiter - whose refusals are worth being able to trace back
    # to the client that caused them.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    # ADDED LAST, RUNS FIRST.
    #
    # Starlette wraps middleware in reverse order of registration, so
    # the last one added is the outermost. That is what this needs: a
    # request id must exist before anything else runs, including the
    # rate limiter - whose refusals are worth being able to trace back
    # to the client that caused them.
    app.add_middleware(RequestContextMiddleware)

    # NO CORSMiddleware, ON PURPOSE.
    #
    # Every request reaches this API through the Next.js rewrite in
    # frontend/next.config.ts, so the browser only ever sees ONE
    # origin. There is no cross-origin request to permit, and an
    # allowlist here would be configuration that does nothing except
    # get copied into a future deployment where it is wrong.
    #
    # If a genuinely cross-origin client ever arrives - a mobile app, a
    # public API - add it THEN, with an explicit list from settings,
    # and never `*` alongside allow_credentials. Do not add it to
    # "fix a CORS error": that error means something bypassed the
    # proxy, and the proxy is the thing keeping SameSite cookies
    # working.

    # One error shape for every failure, carrying the request id the
    # caller was given in X-Request-ID.
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(plugins.router)
    app.include_router(agents.router)
    app.include_router(permissions.router)
    app.include_router(approvals.router)
    app.include_router(conversations.agent_router)
    app.include_router(conversations.router)
    app.include_router(executions.router)
    app.include_router(audit.router)
    app.include_router(limits.router)
    app.include_router(chat.router)
    app.include_router(stream.router)

    return app


# The instance uvicorn imports: `uvicorn api.main:app`.
#
# The factory above is the real entry point; this is a convenience so
# the command line stays short.
app = create_app()
