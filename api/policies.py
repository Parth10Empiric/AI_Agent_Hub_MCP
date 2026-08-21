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


class DatabaseScopePolicy:
    """
    The Phase 5.1 policy: both gates, loaded from the database.

    It answers the executor's one question - "may this agent use this
    tool?" - by asking two independent ones:

        1. did the user switch this tool ON?        agent_tools
        2. does a granted scope COVER it?           agent_scopes

    Both must pass. Neither can widen the other, which is the whole
    reason they are separate:

        enabled but unscoped   the checkbox is on, but the agent was
                               never granted writes  -> DENY
        scoped but disabled    the agent may write GitHub, but the
                               user turned this tool off  -> DENY

    WHY TWO LAYERS INSTEAD OF ONE LIST OF TOOL NAMES

    A tool list is a snapshot. It cannot express "this agent may read
    GitHub" in a way that still holds when github_list_releases ships
    next month - somebody has to remember to tick a new box, and until
    they do the agent is quietly less capable than the user believes.

    A scope is a sentence about capability, so it keeps meaning the
    same thing as the tool catalogue grows. The checkbox layer stays on
    top of it for the fine-grained "yes to issues, no to merges" case.

    WHY IT TAKES SETS AND NOT AN AsyncSession

    check() runs inside the executor's loop, once per requested tool,
    possibly several times per turn. A policy holding a database
    session would turn a permission check into network IO on the hot
    path - and, worse, would make every unit test need PostgreSQL.

    The two sets are loaded ONCE per turn, in chat_service, before the
    executor starts. That also fixes the permission set for the whole
    turn, so a grant cannot change halfway through a multi-tool answer.

    (The flip side is time-of-check to time-of-use: a scope revoked
    mid-turn is honoured only on the NEXT turn. For a turn measured in
    seconds that is acceptable. For an approval that waits minutes for
    a human it is not - which is why Phase 5.2 re-validates on
    resolve.)
    """

    __slots__ = ("_granted", "_enabled", "_agent_name")

    def __init__(
        self,
        granted_scopes: Iterable[str],
        enabled_tools: Iterable[str],
        agent_name: str = "This agent",
    ) -> None:
        self._granted = frozenset(granted_scopes)
        self._enabled = frozenset(enabled_tools)
        self._agent_name = agent_name

    def check(self, tool: ToolDefinition) -> PermissionDecision:

        # --- gate 1: the user's own switch ---------------------------
        #
        # Checked first because it produces the more actionable
        # message. "You turned this off" is one click to fix; "you
        # never granted writes" needs the user to understand scopes.
        if tool.name not in self._enabled:
            return PermissionDecision.deny(
                f"{self._agent_name} has not enabled {tool.name!r}."
            )

        # --- gate 2: the security envelope ---------------------------
        #
        # A plain set intersection, NOT pattern matching.
        #
        # Every tool already carries both its specific and its wildcard
        # scope, baked in at discovery time by build_permissions():
        #
        #     github_create_issue -> ("github:issue:write",
        #                             "github:*:write")
        #
        # so a coarse grant and a fine grant are answered by the same
        # line of code. Glob matching at check time is where permission
        # systems grow subtle holes ("github:*" matching "github-evil");
        # a frozenset intersection cannot have that bug.
        matched = self._granted.intersection(tool.permissions)

        if not matched:
            return PermissionDecision.deny(
                f"'{tool.name}' requires one of "
                f"{list(tool.permissions)}, none of which is granted "
                f"to {self._agent_name}."
            )

        # The reason is stored on the ExecutionRecord, so "why was this
        # allowed?" has an answer that names the specific grant.
        return PermissionDecision.allow(
            f"granted by {sorted(matched)[0]}"
        )

    def permits(self, tool: ToolDefinition) -> bool:
        """Convenience for filtering tool lists before offering them."""

        return self.check(tool).allowed

    # NO __len__ ON PURPOSE.
    #
    # An object defining __len__ is FALSY when empty, and
    # `policy or default_policy()` - which the executor used to do -
    # would then swap the strictest possible policy for AllowAllPolicy.
    # The executor now checks `is None`, but a security object that is
    # falsy in its most locked-down state is a trap for the next such
    # idiom, so this class simply never is.

    def __contains__(self, tool_name: object) -> bool:
        return tool_name in self._enabled
