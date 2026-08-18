from __future__ import annotations

from threading import RLock
from typing import Iterable

from .schemas import Operation, RiskLevel, ToolDefinition


class ToolRegistry:
    """
    Central registry for all tools available to the Agent Engine.

    The registry is responsible only for storing and retrieving tools.
    It does NOT execute tools and does NOT decide which tool the LLM
    should use.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._lock = RLock()

        # Bumped on every mutation.
        #
        # The router caches an expensive inverted index derived from
        # this registry. Rather than have the router rebuild on every
        # query (wasteful) or subscribe to change events (complex), it
        # simply compares this integer against the version it built
        # from. Cheap to maintain, impossible to get a stale index.
        self._version = 0

    @property
    def version(self) -> int:
        """
        Monotonic counter identifying the current registry contents.
        """

        with self._lock:
            return self._version

    def replace_all(
        self,
        tools: Iterable[ToolDefinition],
    ) -> None:
        """
        Replace the current registry with a fresh tool snapshot.
        """

        new_tools: dict[str, ToolDefinition] = {}

        for tool in tools:

            if not tool.name:
                continue

            new_tools[tool.name] = tool

        with self._lock:
            self._tools = new_tools
            self._version += 1

    def register(
        self,
        tool: ToolDefinition,
    ) -> None:
        """
        Register or update a single tool.
        """

        if not tool.name:
            raise ValueError(
                "Tool name cannot be empty."
            )

        with self._lock:
            self._tools[tool.name] = tool
            self._version += 1

    def remove(
        self,
        tool_name: str,
    ) -> bool:
        """
        Remove a tool from the registry.

        Returns True if the tool existed.
        """

        with self._lock:

            existed = (
                self._tools.pop(
                    tool_name,
                    None,
                )
                is not None
            )

            if existed:
                self._version += 1

            return existed

    def get(
        self,
        tool_name: str,
    ) -> ToolDefinition | None:
        """
        Get one tool by exact name.
        """

        with self._lock:
            return self._tools.get(tool_name)

    def require(
        self,
        tool_name: str,
    ) -> ToolDefinition:
        """
        Get a tool or raise an explicit error.
        """

        tool = self.get(tool_name)

        if tool is None:
            raise KeyError(
                f"Tool not found in registry: "
                f"{tool_name}"
            )

        return tool

    def all(self) -> list[ToolDefinition]:
        """
        Return all registered tools.
        """

        with self._lock:
            return list(
                self._tools.values()
            )

    def names(self) -> list[str]:
        """
        Return all registered tool names.
        """

        with self._lock:
            return list(
                self._tools.keys()
            )

    def count(self) -> int:
        """
        Return the number of registered tools.
        """

        with self._lock:
            return len(self._tools)

    def by_server(
        self,
        server: str,
    ) -> list[ToolDefinition]:
        """
        Return tools belonging to a specific
        MCP server.
        """

        with self._lock:
            return [
                tool
                for tool in self._tools.values()
                if tool.server == server
            ]

    def by_namespace(
        self,
        namespace: str,
    ) -> list[ToolDefinition]:
        """
        Return tools belonging to a specific
        service/provider namespace.
        """

        with self._lock:
            return [
                tool
                for tool in self._tools.values()
                if tool.namespace == namespace
            ]

    def servers(self) -> list[str]:
        """
        Return all unique namespaces/services
        represented in the registry.
        """

        with self._lock:
            return sorted(
                {
                    tool.namespace
                    for tool in self._tools.values()
                    if tool.namespace
                }
            )

    def stats(self) -> dict[str, int]:
        """
        Return the number of tools registered
        for each namespace.
        """

        counts: dict[str, int] = {}

        with self._lock:
            for tool in self._tools.values():

                namespace = (
                    tool.namespace
                    or "unknown"
                )

                counts[namespace] = (
                    counts.get(namespace, 0)
                    + 1
                )

        return dict(
            sorted(counts.items())
        )

    def snapshot(
        self,
    ) -> dict[str, ToolDefinition]:
        """
        Return a copy of the current registry mapping.

        The internal registry remains protected
        from external mutation.
        """

        with self._lock:
            return dict(self._tools)

    def clear(self) -> None:
        """
        Remove all registered tools.
        """

        with self._lock:
            self._tools.clear()
            self._version += 1

    def __len__(self) -> int:
        return self.count()

    def by_operation(
        self,
        operation: Operation,
    ) -> list[ToolDefinition]:
        """
        Return tools performing a specific kind of operation.
        """

        with self._lock:
            return [
                tool
                for tool in self._tools.values()
                if tool.operation is operation
            ]

    def read_only(self) -> list[ToolDefinition]:
        """
        Return only the tools that cannot change anything.

        Useful for "safe mode" agents and for the low-confidence
        fallback path, where widening the tool set is safe precisely
        because nothing in it can cause damage.
        """

        with self._lock:
            return [
                tool
                for tool in self._tools.values()
                if tool.read_only
            ]

    def requiring_approval(self) -> list[ToolDefinition]:
        """
        Return tools a human must confirm before execution.
        """

        with self._lock:
            return [
                tool
                for tool in self._tools.values()
                if tool.requires_approval
            ]

    def risk_summary(self) -> dict[str, int]:
        """
        Count tools per risk level.

        Worth printing at startup. If your MCP server exposes eleven
        CRITICAL tools, that is something you want to know before a
        client does.
        """

        counts: dict[str, int] = {
            level.value: 0
            for level in RiskLevel
        }

        with self._lock:
            for tool in self._tools.values():
                counts[tool.risk_level.value] += 1

        return counts
