from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .adapters.ollama import OllamaToolAdapter
from .discovery import ToolDiscovery
from .embeddings import EmbeddingProvider
from .execution import ExecutionRecord
from .executor import ToolExecutor
from .permissions import ApprovalHandler, PermissionPolicy
from .registry import ToolRegistry
from .router import ToolRouter
from .routing import RoutingDecision
from .schemas import ToolDefinition

"""
The Agent Engine facade.

One object that owns discovery, the registry and the router, so callers
do not have to wire three components together correctly. As Phase 2
continues, the executor and the permission engine join the same
constructor and no caller changes.

The engine deliberately keeps TWO views of every tool:

    ToolDefinition  - our normalized model, used for routing,
                      permissions, risk and the future UI
    MCP Tool        - the original SDK object, which the agent loop
                      converts into the LLM's tool schema

They are produced from a single `list_tools()` call and never drift.
Keeping both is what let Phase 2.2 and 2.3 be added without touching
the existing agent loop at all.
"""


class AgentEngine:
    """
    High-level coordinator for the Agent Engine.
    """

    def __init__(
        self,
        server_name: str = "personal-mcp-server",
        embedding_provider: EmbeddingProvider | None = None,
        policy: PermissionPolicy | None = None,
        approval: ApprovalHandler | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:

        self.registry = ToolRegistry()

        self.router = ToolRouter(
            self.registry,
            embedding_provider=embedding_provider,
        )

        # The executor shares the registry, so a tool discovered once
        # is routable and executable with no second registration step
        # and no chance of the two views disagreeing.
        self.executor = ToolExecutor(
            self.registry,
            policy=policy,
            approval=approval,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )

        self.discovery = ToolDiscovery(server_name=server_name)

        # Original MCP SDK tool objects, kept because the agent loop
        # needs them to build the LLM tool schema.
        self._mcp_tools: list[Any] = []

        self._mcp_tools_by_name: dict[str, Any] = {}

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------

    async def discover_tools(self, session: Any) -> int:
        """
        Discover tools from the MCP server.

        One `list_tools()` call produces both representations. Calling
        it twice would risk the two views disagreeing if the server
        changed between calls.
        """

        result = await session.list_tools()

        self._mcp_tools = list(getattr(result, "tools", []))

        self._mcp_tools_by_name = {
            tool.name: tool
            for tool in self._mcp_tools
        }

        tools = self.discovery.discover_from_result(result)

        self.registry.replace_all(tools)

        return self.registry.count()

    # -----------------------------------------------------------------
    # Routing
    # -----------------------------------------------------------------

    def route(
        self,
        query: str,
        *,
        top_k: int | None = None,
        previous_namespaces: tuple[str, ...] | None = None,
        exclude: set[str] | None = None,
        allow: Callable[[ToolDefinition], bool] | None = None,
    ) -> RoutingDecision:
        """
        Choose the tools relevant to one user message.

        `top_k=None` lets the router size the tool budget itself, based
        on how many services the request touches.

        `exclude` drops tools from consideration, which is what makes a
        second routing pass return something genuinely different.

        `allow` drops tools this caller cannot use, so the budget is
        spent only on tools that could actually run. See
        ToolRouter.route.
        """

        return self.router.route(
            query,
            top_k=top_k,
            previous_namespaces=previous_namespaces,
            exclude=exclude,
            allow=allow,
        )

    def route_by_namespace(
        self,
        namespaces: list[str],
    ) -> RoutingDecision:
        """
        Return every tool belonging to the given services.
        """

        return self.router.route_by_namespace(namespaces)

    def warm_embeddings(self) -> int:
        """
        Precompute the semantic index. Startup only - see ToolRouter.warm.

        BLOCKING, and it says so here because the caller has to care:
        embedding 161 tools is one synchronous HTTP round trip, and an
        async server must run it in a thread or it stalls its own event
        loop while starting up.
        """

        return self.router.warm()

    def select_mcp_tools(
        self,
        decision: RoutingDecision,
    ) -> list[Any]:
        """
        Translate a routing decision back into MCP SDK tool objects.

        This is the join between the two views. Doing it here, once,
        keeps the lookup out of the agent loop - and a dict lookup
        instead of the previous list comprehension means routing stays
        O(selected) rather than O(selected x total) as the tool count
        grows.
        """

        selected: list[Any] = []

        for name in decision.tool_names:

            tool = self._mcp_tools_by_name.get(name)

            if tool is not None:
                selected.append(tool)

        return selected

    # -----------------------------------------------------------------
    # Execution
    # -----------------------------------------------------------------

    async def execute(
        self,
        session: Any,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        allowed_tools: set[str] | None = None,
    ) -> ExecutionRecord:
        """
        Run one tool call through validation, permissions and retries.
        """

        return await self.executor.execute(
            session=session,
            tool_name=tool_name,
            arguments=arguments,
            allowed_tools=allowed_tools,
        )

    # -----------------------------------------------------------------
    # Accessors
    # -----------------------------------------------------------------

    def get_tools(self) -> list[ToolDefinition]:
        """
        Return normalized ToolDefinition objects.
        """

        return self.registry.all()

    def get_mcp_tools(self) -> list[Any]:
        """
        Return the original MCP SDK Tool objects.
        """

        return list(self._mcp_tools)

    def get_tool(self, tool_name: str) -> ToolDefinition | None:
        """
        Get one normalized tool definition.
        """

        return self.registry.get(tool_name)

    def tool_count(self) -> int:
        """
        Return the number of discovered tools.
        """

        return self.registry.count()

    def get_ollama_tools(self) -> list[dict[str, Any]]:
        """
        Return all registered tools in Ollama's expected format.
        """

        return OllamaToolAdapter.convert_many(self.registry.all())

    def describe(self) -> dict[str, Any]:
        """
        A compact summary of what was discovered.

        Printed at startup so that a misclassification is visible
        immediately rather than discovered when an agent deletes
        something without asking.
        """

        return {
            "server": self.discovery.server_name,
            "total_tools": self.registry.count(),
            "by_service": self.registry.stats(),
            "by_risk": self.registry.risk_summary(),
            "read_only": len(self.registry.read_only()),
            "requires_approval": len(
                self.registry.requiring_approval()
            ),
        }
