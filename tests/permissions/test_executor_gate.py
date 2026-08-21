from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.types import CallToolResult, TextContent  # noqa: E402

from agent.errors import ErrorCode  # noqa: E402
from agent.execution import ExecutionStatus  # noqa: E402
from agent.executor import ToolExecutor  # noqa: E402
from api.policies import DatabaseScopePolicy  # noqa: E402
from api.scopes import default_scopes  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
The Phase 5 milestone test 1, in code:

    disable / unscope a tool
    ask for it anyway
    -> the executor returns permission_denied
    -> status is DENIED, not FAILED
    -> the MCP server is never called

The last assertion is the one that matters. DENIED means the system
worked; FAILED means it broke. And "session.calls == []" is the proof
that the gate sits BELOW the model: no prompt, however crafted, can
reach the network through a policy that refuses first.
"""


REGISTRY = build_registry()
ALL_NAMES = {tool.name for tool in REGISTRY.all()}


class FakeSession:
    """A session that records calls, so 'never called' is testable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))

        return CallToolResult(
            content=[TextContent(type="text", text='{"ok": true}')],
            isError=False,
        )


def run(coro):
    return asyncio.run(coro)


def _executor(granted: set[str], enabled: set[str]) -> ToolExecutor:
    return ToolExecutor(
        REGISTRY,
        policy=DatabaseScopePolicy(granted, enabled, "Test agent"),
        backoff_base=0.0,
    )


# ---------------------------------------------------------------------


def test_a_write_without_a_scope_is_denied_below_the_model():
    session = FakeSession()

    # The tool is ENABLED - the model was even offered it - but no
    # write scope is granted.
    executor = _executor({"github:*:read"}, ALL_NAMES)

    record = run(
        executor.execute(
            session,
            "github_create_issue",
            # Valid arguments on purpose. The executor validates
            # arguments (step 3) BEFORE it checks permissions (step 4),
            # so a call with missing fields would fail for the wrong
            # reason and this test would prove nothing.
            {"owner": "octocat", "repo": "hello", "title": "hi"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert record.error.code is ErrorCode.PERMISSION_DENIED

    # The security property, stated as an assertion: nothing reached
    # GitHub. No issue was created.
    assert session.calls == []


def test_a_disabled_tool_is_denied_even_with_a_full_scope():
    session = FakeSession()

    executor = _executor({"github:*:write"}, set())

    record = run(
        executor.execute(
            session,
            "github_create_issue",
            {"owner": "octocat", "repo": "hello", "title": "hi"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert record.error.code is ErrorCode.PERMISSION_DENIED
    assert session.calls == []


def test_the_denial_reason_is_recorded():
    session = FakeSession()

    executor = _executor({"github:*:read"}, ALL_NAMES)

    record = run(
        executor.execute(
            session,
            "github_create_issue",
            {"owner": "octocat", "repo": "hello", "title": "hi"},
        )
    )

    # The reason lands on the ExecutionRecord, so the timeline can tell
    # the user WHY - "requires github:issue:write" - instead of showing
    # a bare red cross they cannot act on.
    assert "github:issue:write" in record.error.message


def test_a_granted_read_still_executes():
    session = FakeSession()

    granted = default_scopes(REGISTRY.all())
    enabled = {tool.name for tool in REGISTRY.all() if tool.read_only}

    executor = _executor(granted, enabled)

    record = run(
        executor.execute(
            session,
            "github_list_issues",
            {"owner": "octocat", "repo": "hello"},
        )
    )

    # The gate is not simply "deny everything": a new agent's default
    # grants really do let it read.
    assert record.status is ExecutionStatus.SUCCESS
    assert len(session.calls) == 1


def test_an_empty_policy_is_not_silently_replaced_by_allow_all():
    """
    Regression: the fail-open bug this file found.

    ToolExecutor used to do `self.policy = policy or default_policy()`.
    A policy object defining __len__ is FALSY when it holds nothing, so
    the strictest possible configuration - an agent with no tools
    enabled and no scopes granted - had its policy quietly swapped for
    AllowAllPolicy and could call anything.

    The idiom is everywhere in ordinary Python, which is exactly why it
    is worth a permanent test: `or` asks "is this truthy", and for a
    security object the answer must never decide anything.
    """

    session = FakeSession()

    executor = _executor(set(), set())

    assert executor.policy is not None
    assert type(executor.policy).__name__ == "DatabaseScopePolicy"

    record = run(
        executor.execute(
            session,
            "github_list_issues",
            {"owner": "octocat", "repo": "hello"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert session.calls == []
