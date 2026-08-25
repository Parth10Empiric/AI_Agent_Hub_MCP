from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.permissions import PermissionPolicy  # noqa: E402
from api.policies import AgentToolPolicy, DatabaseScopePolicy  # noqa: E402
from api.scopes import default_scopes  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Tests for DatabaseScopePolicy - the Phase 5.1 gate.

No database, on purpose. The policy takes two plain sets, so these
tests exercise the exact object the executor uses, at the exact
boundary that matters, without a PostgreSQL instance anywhere.

The property under test throughout: BOTH gates must pass. Every test
below is some arrangement of "one gate open, one gate shut".
"""


REGISTRY = build_registry()
TOOLS = REGISTRY.all()

CREATE_ISSUE = REGISTRY.require("github_create_issue")
LIST_ISSUES = REGISTRY.require("github_list_issues")

ALL_NAMES = {tool.name for tool in TOOLS}


# ---------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------


def test_it_satisfies_the_phase_2_protocol():
    # runtime_checkable Protocol: this is what lets the executor take
    # the new policy with no change at all.
    policy = DatabaseScopePolicy(set(), set())

    assert isinstance(policy, PermissionPolicy)
    assert isinstance(AgentToolPolicy(set()), PermissionPolicy)


# ---------------------------------------------------------------------
# Both gates
# ---------------------------------------------------------------------


def test_enabled_and_scoped_is_allowed():
    policy = DatabaseScopePolicy(
        {"github:issue:write"},
        {"github_create_issue"},
    )

    decision = policy.check(CREATE_ISSUE)

    assert decision.allowed
    # The reason names the specific grant, because "why was this
    # allowed?" is a question an auditor asks.
    assert "github:issue:write" in decision.reason


def test_enabled_but_unscoped_is_denied():
    # The checkbox is ticked and the scope is not granted. This is the
    # case the whole phase exists for: a tool switch cannot grant
    # capability on its own.
    policy = DatabaseScopePolicy(
        {"github:*:read"},
        {"github_create_issue"},
    )

    decision = policy.check(CREATE_ISSUE)

    assert not decision.allowed
    assert "github:issue:write" in decision.reason


def test_scoped_but_disabled_is_denied():
    # The mirror case: full write scope, tool switched off.
    policy = DatabaseScopePolicy({"github:*:write"}, set())

    decision = policy.check(CREATE_ISSUE)

    assert not decision.allowed
    assert "not enabled" in decision.reason


def test_neither_is_denied():
    assert not DatabaseScopePolicy(set(), set()).check(CREATE_ISSUE).allowed


# ---------------------------------------------------------------------
# Granularity
# ---------------------------------------------------------------------


def test_a_wildcard_grant_covers_the_specific_tool():
    policy = DatabaseScopePolicy({"github:*:write"}, ALL_NAMES)

    assert policy.check(CREATE_ISSUE).allowed


def test_a_specific_grant_does_not_widen_to_other_resources():
    # "may create issues" must not become "may merge pull requests".
    policy = DatabaseScopePolicy({"github:issue:write"}, ALL_NAMES)

    for tool in TOOLS:

        if tool.read_only or tool.namespace != "github":
            continue

        expected = "github:issue:write" in tool.permissions

        assert policy.check(tool).allowed is expected


def test_a_read_grant_never_permits_a_write():
    policy = DatabaseScopePolicy({"github:*:read"}, ALL_NAMES)

    assert policy.check(LIST_ISSUES).allowed
    assert not policy.check(CREATE_ISSUE).allowed


def test_a_grant_does_not_cross_services():
    policy = DatabaseScopePolicy({"github:*:write"}, ALL_NAMES)

    for tool in TOOLS:
        if tool.namespace != "github":
            assert not policy.check(tool).allowed


# ---------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------


def test_an_unknown_tool_is_denied():
    # A tool added to the MCP server after this agent was configured is
    # not something the user consented to. It must not become available
    # on its own.
    #
    # The enabled set is ALL_NAMES minus this tool's own name, rather
    # than minus a placeholder. The placeholder version of this test
    # quietly stopped testing anything the day a real
    # github_delete_repository tool shipped: the "unknown" name was in
    # ALL_NAMES, so the enabled gate opened and the assertion failed on
    # a policy that was working correctly. Subtracting the name under
    # test cannot rot that way.
    class Unknown:
        name = "github_delete_everything_everywhere"
        permissions = ("github:repository:admin", "github:*:admin")

    policy = DatabaseScopePolicy(
        {"github:*:admin"},
        ALL_NAMES - {Unknown.name},
    )

    assert Unknown.name not in ALL_NAMES, (
        "this test needs a name no real tool uses"
    )

    assert not policy.check(Unknown()).allowed


def test_an_empty_grant_set_denies_everything():
    policy = DatabaseScopePolicy(set(), ALL_NAMES)

    assert all(not policy.check(tool).allowed for tool in TOOLS)


def test_a_malformed_scope_grants_nothing():
    # Defence in depth: validate_scope should stop this reaching the
    # database, but if a row ever does hold "github:*" it must not
    # accidentally match anything.
    policy = DatabaseScopePolicy({"github:*", "github", "*"}, ALL_NAMES)

    assert all(not policy.check(tool).allowed for tool in TOOLS)


# ---------------------------------------------------------------------
# The new-agent default
# ---------------------------------------------------------------------


def test_a_new_agent_can_read_everything_and_write_nothing():
    # Exactly the state agent_service.create_agent produces: read
    # wildcards granted, and enabled = tool.read_only.
    granted = default_scopes(TOOLS)
    enabled = {tool.name for tool in TOOLS if tool.read_only}

    policy = DatabaseScopePolicy(granted, enabled)

    for tool in TOOLS:
        assert policy.check(tool).allowed is tool.read_only


def test_ticking_a_write_tool_alone_does_not_grant_writes():
    # A user switching on github_create_issue in the tools screen, with
    # no scope granted, still cannot write. Two deliberate actions are
    # required - which is the security property Phase 5.1 adds.
    granted = default_scopes(TOOLS)
    enabled = {tool.name for tool in TOOLS if tool.read_only}
    enabled.add("github_create_issue")

    policy = DatabaseScopePolicy(granted, enabled)

    assert not policy.check(CREATE_ISSUE).allowed


def test_permits_matches_check():
    policy = DatabaseScopePolicy({"github:*:read"}, ALL_NAMES)

    for tool in TOOLS:
        assert policy.permits(tool) is policy.check(tool).allowed
