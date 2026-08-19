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
    auth,
    chat,
    conversations,
    health,
    plugins,
    stream,
)
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

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(plugins.router)
    app.include_router(agents.router)
    app.include_router(conversations.agent_router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(stream.router)

    return app


# The instance uvicorn imports: `uvicorn api.main:app`.
#
# The factory above is the real entry point; this is a convenience so
# the command line stays short.
app = create_app()
