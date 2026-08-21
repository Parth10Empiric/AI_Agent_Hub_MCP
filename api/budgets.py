from __future__ import annotations

import uuid

from agent.permissions import BudgetDecision
from agent.schemas import RiskLevel, ToolDefinition

from api.ratelimit import RateLimiter
from api.settings import APISettings


"""
The volume limits, as the executor sees them (Phase 5.7).

Three budgets, on two different keys, because they answer different
questions:

    tool calls    per USER, per hour     COST
                  every call is an API request and a slice of the
                  model's context

    write ops     per AGENT, per hour    BLAST RADIUS
                  the agent is the unit a user configures, and the
                  unit that gets compromised

    dangerous     per AGENT, per hour    BLAST RADIUS, tightened
                  keyed on RISK, not operation

WHY RISK AND NOT OPERATION FOR THE LAST ONE

Phase5.md says "CRITICAL ops, 5/hour". This project has two independent
axes, and they do not line up:

    Operation   read | write | delete | admin       what the verb does
    RiskLevel   safe | low | medium | high | critical   blast radius

google_drive_create_permission - the tool the whole prompt-injection
story is about - is an ADMIN operation and HIGH risk. A limit keyed on
`risk == critical` would not cover it, and a limit keyed on operation
would miss slack_send_message, which is an ordinary WRITE that cannot
be unsent.

So the tight limit covers HIGH and CRITICAL, and the broad one covers
anything that mutates. Every dangerous call is counted twice, on
purpose: the specific budget is what stops it, the general one is what
notices the pattern.
"""


# Anything at or above this level counts against the tight budget.
DANGEROUS_LEVELS = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})

HOUR = 3600


class AgentBudget:
    """
    Implements the Phase 5.7 ToolBudget protocol.

    Built per TURN, like DatabaseScopePolicy - it holds the ids and a
    reference to the shared limiter, nothing else.

    ORDER OF CHECKS MATTERS. The tightest, most specific budget is
    checked first, so the message the user sees names the real
    constraint. Told "you have used your hourly tool calls" when the
    truth is "you have shared five files this hour" they will retry,
    fail again, and file a bug.
    """

    __slots__ = ("_limiter", "_settings", "_user_id", "_agent_id")

    def __init__(
        self,
        limiter: RateLimiter,
        settings: APISettings,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        self._limiter = limiter
        self._settings = settings
        self._user_id = user_id
        self._agent_id = agent_id

    async def check(self, tool: ToolDefinition) -> BudgetDecision:

        if not self._settings.rate_limit_enabled:
            return BudgetDecision(allowed=True)

        # --- the tight one, first ------------------------------------
        if tool.risk_level in DANGEROUS_LEVELS:

            decision = self._limiter.check(
                key(self._agent_id, "dangerous"),
                self._settings.dangerous_ops_per_hour,
                HOUR,
            )

            if not decision.allowed:
                return BudgetDecision(
                    allowed=False,
                    reason=(
                        f"This agent has already performed "
                        f"{decision.limit} high-risk actions in the last "
                        f"hour. Try again in "
                        f"{_friendly(decision.retry_after)}."
                    ),
                    retry_after=decision.retry_after,
                )

        # --- anything that changes something -------------------------
        if not tool.read_only:

            decision = self._limiter.check(
                key(self._agent_id, "write"),
                self._settings.write_ops_per_hour,
                HOUR,
            )

            if not decision.allowed:
                return BudgetDecision(
                    allowed=False,
                    reason=(
                        f"This agent has already made {decision.limit} "
                        f"changes in the last hour. Try again in "
                        f"{_friendly(decision.retry_after)}."
                    ),
                    retry_after=decision.retry_after,
                )

        # --- cost, per user ------------------------------------------
        #
        # Checked LAST because it is the least specific. It is also the
        # only one counted for reads, which are the overwhelming
        # majority of calls.
        decision = self._limiter.check(
            key(self._user_id, "tool_calls"),
            self._settings.tool_calls_per_hour,
            HOUR,
        )

        if not decision.allowed:
            return BudgetDecision(
                allowed=False,
                reason=(
                    f"You have used {decision.limit} tool calls in the "
                    f"last hour. Try again in "
                    f"{_friendly(decision.retry_after)}."
                ),
                retry_after=decision.retry_after,
            )

        return BudgetDecision(allowed=True)


def key(subject: uuid.UUID | str, bucket: str) -> str:
    """
    One namespaced key per (subject, bucket).

    Namespaced because a user id and an agent id are both UUIDs, and a
    collision between "this user's tool calls" and "this agent's
    writes" would be silent and very hard to see.
    """

    return f"{bucket}:{subject}"


def _friendly(seconds: int) -> str:
    """
    "about 20 minutes", not "1187 seconds".

    The user is being told to wait. A number they have to divide is a
    number they will get wrong, and precision here is false anyway -
    the window slides.
    """

    if seconds <= 60:
        return "less than a minute"

    minutes = round(seconds / 60)

    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"

    hours = round(seconds / 3600)

    return f"about {hours} hour{'s' if hours != 1 else ''}"
