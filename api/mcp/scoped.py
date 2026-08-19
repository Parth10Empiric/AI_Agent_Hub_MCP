from __future__ import annotations

from typing import Any

from api.mcp.provider import MCPSessionProvider


class ScopedSession:
    """
    Looks like a ClientSession to the executor, but holds the shared
    MCP lock for ONE tool call at a time.

    THIS CLASS IS THE REASON CONCURRENCY SURVIVES PHASE 3.8.

    An agent turn looks like this:

        route          3-9 ms      pure CPU
        LLM call       2-20 s      network, no MCP involved
        tool call      0.1-2 s     needs the MCP session
        LLM call       2-20 s      network again
        tool call      0.1-2 s
        LLM call       2-20 s

    The naive wiring is to borrow the session for the whole turn:

        async with provider.session() as mcp:
            await run_agent(session=mcp, ...)

    That holds an EXCLUSIVE lock across every LLM call in the turn. One
    user's 30-second conversation would block every other user for 30
    seconds - undoing the AsyncClient fix from the preparation phase,
    and for exactly the same reason: something slow held something
    shared.

    Passing this proxy instead means the lock is taken only around
    `call_tool`, which is the part that genuinely needs it:

        held      ~0.1-2 s per tool call
        not held  every LLM call, all routing, all database work

    The executor calls exactly one method on the session
    (`session.call_tool`, executor.py line 690), so a proxy this small
    is a complete substitute.
    """

    __slots__ = ("_provider", "_user_id")

    def __init__(
        self,
        provider: MCPSessionProvider,
        user_id: str | None = None,
    ) -> None:
        self._provider = provider

        # Carried through to provider.session() so Phase 5 can resolve
        # per-user credentials without this class changing.
        self._user_id = user_id

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:

        async with self._provider.session(self._user_id) as session:
            return await session.call_tool(name, arguments)

    async def list_tools(self) -> Any:
        async with self._provider.session(self._user_id) as session:
            return await session.list_tools()
