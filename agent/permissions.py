from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .schemas import RiskLevel, ToolDefinition

"""
Permission and approval seams.

Phase2.md lists "check permissions" as a step in the executor pipeline,
and AI_Agent_Hub.md section 14 wants human approval before write
operations. Full permission MANAGEMENT - per-agent grants stored in
PostgreSQL, a UI with checkboxes - is Phase 5 work and is not built
here.

What IS built here is the decision point those features will plug into.

That distinction matters. If the executor called the database directly,
Phase 5 would mean surgery on the executor. Instead the executor
depends on two tiny interfaces, and Phase 5 becomes "write a new class
that implements PermissionPolicy". Nothing else changes.

This is the same pattern used for embeddings in the router: define the
seam early, ship a trivial implementation, swap it later.

Two separate questions, deliberately kept apart:

    PermissionPolicy  - MAY this agent ever use this tool?
                        Configuration. Decided once, per agent.

    ApprovalHandler   - should we do this PARTICULAR call right now?
                        A human judgement, made in the moment.

Permission is about capability, approval is about consent. An agent can
hold permission to send Slack messages and still need a human to okay
this specific message to this specific customer.
"""


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """
    The answer to "may this agent use this tool?"

    Carries a reason even when allowed, because that reason ends up in
    the execution record - and "why was this permitted?" is a question
    auditors ask.
    """

    allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> PermissionDecision:
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> PermissionDecision:
        return cls(allowed=False, reason=reason)


@runtime_checkable
class PermissionPolicy(Protocol):
    """
    Anything that can decide whether a tool may be used.
    """

    def check(self, tool: ToolDefinition) -> PermissionDecision:
        ...


class AllowAllPolicy:
    """
    The default: no restrictions.

    Correct for a single-user CLI where you are the only operator. It
    is NOT correct once Agent Hub has real users, which is exactly why
    the executor takes a policy rather than assuming one.
    """

    __slots__ = ()

    def check(self, tool: ToolDefinition) -> PermissionDecision:
        return PermissionDecision.allow("no policy configured")


class ReadOnlyPolicy:
    """
    Blocks anything that can change state.

    Genuinely useful today: run the agent against a client's real
    account to demonstrate it, with a hard guarantee that it cannot
    modify anything. That guarantee lives here, one layer below the
    LLM, so no amount of prompt injection can talk its way past it.
    """

    __slots__ = ()

    def check(self, tool: ToolDefinition) -> PermissionDecision:

        if tool.read_only:
            return PermissionDecision.allow("read-only tool")

        return PermissionDecision.deny(
            f"'{tool.name}' performs a {tool.operation.value} "
            "operation and this agent is read-only."
        )


class MaxRiskPolicy:
    """
    Blocks tools above a risk ceiling.

    Lets you say "this agent may write, but never do anything
    CRITICAL" without enumerating tools by name - which matters
    because tool lists grow and enumerations go stale.
    """

    __slots__ = ("_ceiling",)

    def __init__(
        self,
        ceiling: RiskLevel = RiskLevel.MEDIUM,
    ) -> None:
        self._ceiling = ceiling

    def check(self, tool: ToolDefinition) -> PermissionDecision:

        if tool.risk_level.severity <= self._ceiling.severity:
            return PermissionDecision.allow(
                f"risk {tool.risk_level.value} within ceiling"
            )

        return PermissionDecision.deny(
            f"'{tool.name}' is {tool.risk_level.value} risk, above "
            f"the configured ceiling of {self._ceiling.value}."
        )


class ScopePolicy:
    """
    Grant-based permissions, the shape Phase 5 will store per agent.

    Works against the scopes Phase 2.2 attached to every tool:

        github_create_issue -> ("github:issue:write", "github:*:write")

    A tool is allowed if the agent holds ANY of its scopes. That is
    what makes both granularities work with one check:

        {"github:*:read"}         every GitHub read
        {"github:issue:write"}    create/update issues, nothing else

    Note what this does NOT do: parse wildcards at check time. The
    wildcard form was baked into each tool back in discovery, so this
    is a plain set intersection - fast, and impossible to get subtly
    wrong with pattern matching.
    """

    __slots__ = ("_granted",)

    def __init__(self, granted_scopes: set[str]) -> None:
        self._granted = frozenset(granted_scopes)

    def check(self, tool: ToolDefinition) -> PermissionDecision:

        matched = self._granted.intersection(tool.permissions)

        if matched:
            return PermissionDecision.allow(
                f"granted by {sorted(matched)[0]}"
            )

        return PermissionDecision.deny(
            f"'{tool.name}' requires one of "
            f"{list(tool.permissions)}, none of which is granted."
        )


# ---------------------------------------------------------------------
# Human approval
# ---------------------------------------------------------------------


@runtime_checkable
class ApprovalHandler(Protocol):
    """
    Anything that can ask a human to confirm a call.

    Async because the real implementations are: a web UI waiting on a
    websocket, a Slack confirmation button, a terminal prompt. All of
    them wait on a human, and a human is slow.

    TWO METHODS, NOT ONE (Phase 5.2)

        requires()   should we ask about this tool at all?
        request()    ask, and wait for the answer.

    The first used to live in the executor as a bare
    `if tool.requires_approval:` - the value Phase 2 CLASSIFIED. That
    made the per-agent setting in agent_tools.requires_approval
    unreachable: a user could tick "always ask before this read" and be
    ignored, or untick approval on a write and still be prompted.

    Moving the question into the handler puts it where the agent's
    configuration already is. The executor asks "does this need a
    human?" instead of deciding for itself, and every implementation
    below answers it the way it always did.
    """

    def requires(self, tool: ToolDefinition) -> bool:
        ...

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:
        ...


class AutoApprove:
    """
    Approve everything without asking. The default.
    """

    __slots__ = ()

    def requires(self, tool: ToolDefinition) -> bool:
        return False

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:
        return True


class ConsoleApproval:
    """
    Terminal confirmation, implementing the rule from
    AI_Agent_Hub.md section 14: reads run automatically, writes ask.

    Note `asyncio.to_thread` around `input()`. This is not decoration.

    `input()` blocks the thread it runs on. In an async program that
    thread is running the event loop, so a blocking `input()` freezes
    EVERYTHING - timers, the MCP connection's background reads, any
    other task. In this CLI you might get away with it. In Phase 3's
    FastAPI server it would stall every other user's request while one
    person decides whether to approve a Slack message.

    `to_thread` moves the blocking call onto a worker thread and hands
    the event loop back an awaitable. The rule to remember: never call
    a blocking function directly inside async code.
    """

    __slots__ = ()

    def requires(self, tool: ToolDefinition) -> bool:
        return tool.requires_approval

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:

        if not self.requires(tool):
            return True

        print("\n" + "-" * 52)
        print("APPROVAL REQUIRED")
        print(f"  Tool      : {tool.name}")
        print(f"  Operation : {tool.operation.value}")
        print(f"  Risk      : {tool.risk_level.value}")

        for key, value in arguments.items():
            print(f"  {key:<10}: {value}")

        print("-" * 52)

        answer = await asyncio.to_thread(
            input,
            "Approve? [y/N]: ",
        )

        return answer.strip().lower() in {"y", "yes"}


class DenyAll:
    """
    Refuse every call that needs approval. Useful in tests and in
    unattended runs where nobody is present to answer.
    """

    __slots__ = ()

    def requires(self, tool: ToolDefinition) -> bool:
        return tool.requires_approval

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:
        return not self.requires(tool)


# ---------------------------------------------------------------------
# Budgets (Phase 5.7)
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """
    The answer to "is there budget left for this call?"

    `retry_after` is in seconds, and it is the point: a refusal with no
    idea when to come back either stops the user working or turns a
    polite client into a hot loop.
    """

    allowed: bool
    reason: str = ""
    retry_after: int = 0


@runtime_checkable
class ToolBudget(Protocol):
    """
    Anything that can say "not so many, not so fast".

    A THIRD seam beside PermissionPolicy and ApprovalHandler, because
    it answers a third question:

        policy     MAY this agent ever do this?      capability
        approval   should we do THIS one, now?       consent
        budget     how many, in the last hour?       volume

    Volume is invisible to the other two. Each call in a mass
    exfiltration looks exactly like the one the user asked for; only
    the count gives it away.

    Async, unlike PermissionPolicy, because the real implementation
    will eventually ask Redis.
    """

    async def check(self, tool: ToolDefinition) -> BudgetDecision:
        ...


class NoBudget:
    """
    Unlimited. The default, and correct for the CLI.

    A single operator running their own tools does not need protecting
    from themselves - and a limit in a script is a limit that fires at
    3am with nobody to see it.
    """

    __slots__ = ()

    async def check(self, tool: ToolDefinition) -> BudgetDecision:
        return BudgetDecision(allowed=True)


def default_budget() -> ToolBudget:
    return NoBudget()


def default_policy() -> PermissionPolicy:
    return AllowAllPolicy()


def default_approval() -> ApprovalHandler:
    return AutoApprove()


__all__ = [
    "ApprovalHandler",
    "BudgetDecision",
    "AutoApprove",
    "AllowAllPolicy",
    "ConsoleApproval",
    "DenyAll",
    "MaxRiskPolicy",
    "NoBudget",
    "PermissionDecision",
    "PermissionPolicy",
    "ReadOnlyPolicy",
    "ScopePolicy",
    "ToolBudget",
    "default_approval",
    "default_budget",
    "default_policy",
]
