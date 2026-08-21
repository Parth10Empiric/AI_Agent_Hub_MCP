from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.errors import ErrorCode  # noqa: E402
from agent.execution import ExecutionStatus  # noqa: E402
from agent.executor import ToolExecutor  # noqa: E402
from agent.permissions import NoBudget  # noqa: E402
from api.budgets import AgentBudget, key  # noqa: E402
from api.ratelimit import InMemoryRateLimiter  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Rate limiting (Phase 5.7).

No database, no HTTP. A limiter is a counter and a clock, and both are
things a unit test can hold still.
"""


REGISTRY = build_registry()

READ = REGISTRY.require("github_list_issues")
WRITE = REGISTRY.require("github_create_issue")
DANGEROUS = REGISTRY.require("slack_send_message")   # WRITE, but HIGH risk


class Settings:
    rate_limit_enabled = True
    tool_calls_per_hour = 300
    write_ops_per_hour = 50
    dangerous_ops_per_hour = 5


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------


def test_it_allows_exactly_n_then_refuses():
    limiter = InMemoryRateLimiter()

    allowed = [limiter.check("k", 3, 60).allowed for _ in range(5)]

    assert allowed == [True, True, True, False, False]


def test_a_refusal_says_when_to_come_back():
    limiter = InMemoryRateLimiter()

    for _ in range(3):
        limiter.check("k", 3, 60)

    decision = limiter.check("k", 3, 60)

    # Not just "no". A refusal with no "when" turns a polite client
    # into a hot loop, which is what the limit exists to stop.
    assert decision.allowed is False
    assert 0 < decision.retry_after <= 61


def test_the_window_slides_rather_than_resetting():
    """
    The fixed-window bug, written down so nobody 'simplifies' the
    deque into a counter.

    A counter per clock-hour lets 2x the limit through in seconds:

        10:59:58   30 turns   "within limit"
        11:00:02   30 turns   "within limit"
    """

    limiter = InMemoryRateLimiter()

    # A one-second window, filled.
    for _ in range(3):
        limiter.check("k", 3, 1)

    assert limiter.check("k", 3, 1).allowed is False

    time.sleep(1.05)

    # The old events aged out one at a time - they did not all reset
    # on a boundary.
    assert limiter.check("k", 3, 1).allowed is True


def test_peek_reports_without_spending():
    limiter = InMemoryRateLimiter()

    for _ in range(10):
        assert limiter.peek("k", 3, 60).remaining == 3

    # A usage endpoint that consumed budget would make the meter wrong
    # by exactly the number of times the user looked at it.
    assert limiter.check("k", 3, 60).allowed is True


def test_keys_are_independent():
    limiter = InMemoryRateLimiter()

    for _ in range(3):
        limiter.check("user:a", 3, 60)

    # One user exhausting their budget must not touch another's.
    assert limiter.check("user:b", 3, 60).allowed is True


def test_the_namespace_keeps_user_and_agent_buckets_apart():
    # Both are UUIDs. A collision between "this user's tool calls" and
    # "this agent's writes" would be silent and very hard to see.
    subject = uuid.uuid4()

    assert key(subject, "turns") != key(subject, "write")


def test_the_key_store_is_bounded():
    limiter = InMemoryRateLimiter(max_keys=100)

    for i in range(500):
        limiter.check(f"user:{i}", 10, 60)

    # Otherwise it is one entry per user who ever made a request,
    # forever.
    assert len(limiter._windows) <= 100


# ---------------------------------------------------------------------
# The executor gate
# ---------------------------------------------------------------------


def _budget(limiter) -> AgentBudget:
    return AgentBudget(limiter, Settings(), uuid.uuid4(), uuid.uuid4())


def test_reads_do_not_count_against_the_write_budget():
    limiter = InMemoryRateLimiter()
    budget = _budget(limiter)

    for _ in range(60):
        assert run(budget.check(READ)).allowed is True


def test_writes_are_limited_per_agent():
    limiter = InMemoryRateLimiter()
    budget = _budget(limiter)

    results = [run(budget.check(WRITE)).allowed for _ in range(52)]

    assert results.count(True) == Settings.write_ops_per_hour
    assert results[-1] is False


def test_high_risk_tools_hit_a_much_tighter_budget():
    """
    slack_send_message is an ordinary WRITE and HIGH risk - it cannot
    be unsent. A limit keyed on operation alone would give it fifty a
    hour; keyed on risk it gets five.
    """

    limiter = InMemoryRateLimiter()
    budget = _budget(limiter)

    results = [run(budget.check(DANGEROUS)).allowed for _ in range(7)]

    assert results.count(True) == Settings.dangerous_ops_per_hour


def test_the_message_names_the_budget_that_actually_ran_out():
    limiter = InMemoryRateLimiter()
    budget = _budget(limiter)

    for _ in range(Settings.dangerous_ops_per_hour):
        run(budget.check(DANGEROUS))

    decision = run(budget.check(DANGEROUS))

    # Told "you have used your hourly tool calls" when the truth is
    # "you have sent five messages", a user retries, fails again, and
    # files a bug.
    assert "high-risk" in decision.reason
    assert decision.retry_after > 0


def test_an_exhausted_budget_denies_before_the_network():
    limiter = InMemoryRateLimiter()

    class Session:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments=None):
            self.calls.append(name)
            raise AssertionError("the tool was called anyway")

    session = Session()

    executor = ToolExecutor(
        REGISTRY,
        budget=_budget(limiter),
        backoff_base=0.0,
    )

    for _ in range(Settings.dangerous_ops_per_hour):
        limiter.check(
            key(executor.budget._agent_id, "dangerous"),
            Settings.dangerous_ops_per_hour,
            3600,
        )

    record = run(
        executor.execute(
            session,
            DANGEROUS.name,
            # Valid arguments: the executor validates (step 3)
            # BEFORE it checks the budget (step 4b), so a call with
            # missing fields would fail for the wrong reason.
            {"channel_id": "C1", "text": "hi"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert record.error.code is ErrorCode.BUDGET_EXCEEDED
    assert session.calls == []


def test_budget_exceeded_is_never_retried():
    """
    Distinct from RATE_LIMITED on purpose.

    A 429 from GitHub means "I did not run this, try again" - so
    RATE_LIMITED is in ALWAYS_RETRYABLE. Our own budget is the
    opposite: retrying inside the window cannot succeed, and retrying
    is exactly what the limit exists to prevent.
    """

    from agent.errors import is_retryable
    from agent.schemas import Operation

    for operation in Operation:
        assert not is_retryable(ErrorCode.BUDGET_EXCEEDED, operation)


def test_no_budget_is_the_default_and_allows_everything():
    # Correct for the CLI: a single operator running their own tools
    # does not need protecting from themselves.
    executor = ToolExecutor(REGISTRY)

    assert isinstance(executor.budget, NoBudget)

    for tool in REGISTRY.all():
        assert run(executor.budget.check(tool)).allowed is True


def test_the_limiter_can_be_switched_off():
    class Off(Settings):
        rate_limit_enabled = False

    budget = AgentBudget(
        InMemoryRateLimiter(), Off(), uuid.uuid4(), uuid.uuid4()
    )

    # A limiter nobody can disable is a limiter nobody dares tune.
    for _ in range(100):
        assert run(budget.check(DANGEROUS)).allowed is True
