from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.logging import get_logger


logger = get_logger(__name__)


# api/mcp/provider.py -> api/mcp -> api -> the project root.
#
# server.py MUST be addressed absolutely. A relative "server.py" is
# resolved against the CURRENT WORKING DIRECTORY, so the MCP subprocess
# starts only when the API happens to be launched from the project
# root - and fails with "can't open file" from anywhere else: a
# systemd unit with a different WorkingDirectory, a test run from
# another folder, a Docker entrypoint.
#
# This is the identical bug config.settings.resolve_path() was written
# to fix for the Google token, in a new place. Relative paths in a
# subprocess are a recurring trap, not a one-off.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

MCP_SERVER_SCRIPT = PROJECT_ROOT / "server.py"


@runtime_checkable
class MCPSessionProvider(Protocol):
    """
    Somewhere to get an MCP session from.

    Every caller goes through `session()` and never constructs a
    ClientSession itself. That single rule is what makes the transport
    swappable: endpoints written against this protocol keep working
    when the implementation becomes a pool, or an HTTP client talking
    to a remote MCP service.

    `user_id` is accepted but unused today, because the MCP server is
    still single-tenant (services/github reads one global GITHUB_TOKEN).
    Phase 5 makes it meaningful. It is in the signature NOW so that
    adding multi-tenancy later does not change every call site - the
    hard part of that migration is the call sites, not the lookup.
    """

    @asynccontextmanager
    def session(
        self,
        user_id: str | None = None,
    ) -> AsyncIterator[ClientSession]:
        ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    @property
    def healthy(self) -> bool: ...


class SharedSessionProvider:
    """
    ONE MCP subprocess, shared by every request, guarded by a lock.

    WHY A LOCK

    An MCP stdio session is a single pair of pipes carrying a JSON-RPC
    conversation. Two coroutines writing to it concurrently interleave
    their messages and corrupt the stream - the same hazard as sharing
    one database session between requests.

    The lock makes tool calls SERIAL. That is a real limitation and it
    is the accepted trade for Phase 3:

        tool calls           serialised    <- this lock
        everything else      concurrent    <- routing, LLM calls, SQL

    Routing is 3-9ms of pure CPU and the LLM call is the slow part of a
    turn, so the practical ceiling is far higher than "one user at a
    time". When it does become the bottleneck, swap in a pool - the
    interface does not change.

    WHY A BACKGROUND TASK

    stdio_client and ClientSession are async context managers. They
    must be entered and exited in the SAME task, or anyio raises
    "cancel scope in a different task". FastAPI's lifespan startup and
    shutdown are not guaranteed to be the same task, so the session is
    owned by a dedicated task that is signalled to shut down, rather
    than being entered in startup and exited in shutdown.
    """

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        *,
        startup_timeout: float = 60.0,
    ) -> None:

        # sys.executable, not "python": inside a venv, a bare "python"
        # may resolve to a different interpreter that lacks this
        # project's dependencies. The subprocess must be the SAME
        # interpreter running the API.
        self._command = command or sys.executable
        self._args = args or [str(MCP_SERVER_SCRIPT)]
        self._startup_timeout = startup_timeout

        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._shutdown: asyncio.Event = asyncio.Event()
        self._error: BaseException | None = None

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    async def _run(self) -> None:
        """
        Own the subprocess for its whole life, in one task.

        Enters the context managers, signals readiness, then parks on
        the shutdown event. Everything unwinds in this same task.
        """

        try:
            async with AsyncExitStack() as stack:

                read, write = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=self._command,
                            args=self._args,

                            # The subprocess inherits OUR working
                            # directory otherwise, and the MCP server
                            # resolves its own relative paths - the
                            # Google token, credentials.json - against
                            # it.
                            cwd=str(PROJECT_ROOT),
                        )
                    )
                )

                session = await stack.enter_async_context(
                    ClientSession(read, write)
                )

                await session.initialize()

                self._session = session
                self._ready.set()

                logger.info("MCP session ready")

                await self._shutdown.wait()

        except Exception as exc:
            # Record the failure and release anyone waiting, so a
            # broken MCP server surfaces as a startup error instead of
            # hanging until the timeout.
            self._error = exc
            logger.error("MCP session failed: %s", exc)
            self._ready.set()

        finally:
            self._session = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="mcp-session")

        try:
            await asyncio.wait_for(
                self._ready.wait(),
                timeout=self._startup_timeout,
            )

        except TimeoutError as exc:
            raise RuntimeError(
                "MCP server did not become ready in "
                f"{self._startup_timeout}s"
            ) from exc

        if self._error is not None:
            raise RuntimeError(
                f"MCP server failed to start: {self._error}"
            ) from self._error

    async def stop(self) -> None:
        self._shutdown.set()

        if self._task is None:
            return

        try:
            # The subprocess gets a moment to exit cleanly. If it will
            # not, cancel rather than hanging the whole shutdown - a
            # server that refuses to stop must not stop the deploy.
            await asyncio.wait_for(self._task, timeout=10.0)

        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()

        except Exception as exc:
            logger.warning("MCP shutdown error (ignored): %s", exc)

        finally:
            self._task = None
            logger.info("MCP session closed")

    # -----------------------------------------------------------------
    # Use
    # -----------------------------------------------------------------

    @asynccontextmanager
    async def session(
        self,
        user_id: str | None = None,
    ) -> AsyncIterator[ClientSession]:
        """
        Borrow the session for the duration of the block.

        Hold it for ONE tool call, never for a whole agent turn: the
        lock is exclusive, and holding it across an LLM call would
        block every other user for the length of that call.
        """

        if self._session is None:
            raise RuntimeError("MCP session is not available")

        async with self._lock:
            yield self._session

    @property
    def healthy(self) -> bool:
        return self._session is not None and self._error is None
