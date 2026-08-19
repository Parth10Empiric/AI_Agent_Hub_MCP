from __future__ import annotations

from collections.abc import Iterable

from agent.permissions import PermissionDecision
from agent.schemas import ToolDefinition


class AgentToolPolicy:
    """
    A PermissionPolicy backed by one agent's stored tool settings.

    Implements the Phase 2 protocol, so the executor takes it without
    modification:

        def check(self, tool: ToolDefinition) -> PermissionDecision

    WHY THIS EXISTS SEPARATELY FROM ROUTING

    The router and the policy answer different questions, and both have
    to be asked:

        router   which tools are RELEVANT to this message?
        policy   which tools is this agent ALLOWED to use?

    Routing is a ranking. It is advisory, it runs before the model
    chooses, and a clever prompt can influence which tools look
    relevant. The policy is a gate: it runs inside the executor, AFTER
    the model has asked for something specific, and no prompt can talk
    its way past it because it never sees the prompt.

    That separation is what makes prompt injection survivable. The
    worst a malicious instruction can achieve is to make the model
    request a tool the agent was never granted - and that request is
    refused here, one layer below the LLM.
    """

    __slots__ = ("_enabled", "_agent_name")

    def __init__(
        self,
        enabled_tools: Iterable[str],
        agent_name: str = "This agent",
    ) -> None:
        self._enabled = frozenset(enabled_tools)
        self._agent_name = agent_name

    def check(self, tool: ToolDefinition) -> PermissionDecision:

        if tool.name in self._enabled:
            return PermissionDecision.allow(
                f"enabled on agent {self._agent_name!r}"
            )

        # DENY BY DEFAULT.
        #
        # An unknown tool is refused rather than allowed. A tool added
        # to the MCP server after this agent was configured is not
        # something the user consented to, so it must not become
        # available on its own.
        return PermissionDecision.deny(
            f"{self._agent_name} has not been granted "
            f"{tool.name!r}."
        )

    def __len__(self) -> int:
        return len(self._enabled)

    def __contains__(self, tool_name: object) -> bool:
        return tool_name in self._enabled
