from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.db.models import AgentTool  # noqa: E402
from api.policies import DatabaseScopePolicy  # noqa: E402
from api.services.agent_service import _tool_rows_to_read  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Does the tool list tell the truth about what can actually run?

THE BUG THIS FILE EXISTS FOR

A user ticked every GitHub write tool, saved, and the settings screen
showed eight green checkboxes. Their agent then reported itself
read-only - correctly, because no write SCOPE had been granted and
DatabaseScopePolicy filtered every write tool out before the model ever
saw one.

Nothing was broken. Every layer did exactly what it was designed to do.
The failure was that the ONE screen showing tool configuration could
not see the second gate, so it rendered an unusable tool as ready and
left the user with no way to find out why.

`permitted` closes that, and these tests hold it closed. They are also
the reason the flag is computed on the SERVER: the check here and the
check in the executor come from the same scope data, so the screen and
the gate cannot disagree.
"""


REGISTRY = build_registry()

CREATE_ISSUE = REGISTRY.require("github_create_issue")
LIST_ISSUES = REGISTRY.require("github_list_issues")

TOOLS_BY_NAME = {tool.name: tool for tool in REGISTRY.all()}


def _rows(*names: str) -> list[AgentTool]:
    """Stored rows for these tools, all switched ON."""

    return [
        AgentTool(
            agent_id=None,
            tool_name=name,
            namespace=TOOLS_BY_NAME[name].namespace or "",
            enabled=True,
            requires_approval=False,
        )
        for name in names
    ]


def _read(names: list[str], granted: set[str] | None):
    out = _tool_rows_to_read(_rows(*names), TOOLS_BY_NAME, granted)

    return {tool.tool_name: tool for tool in out}


# ---------------------------------------------------------------------
# The flag itself
# ---------------------------------------------------------------------


def test_a_read_scope_does_not_make_a_write_tool_usable():
    """
    The exact configuration the user had.

    Reads granted, every write tool ticked on, and the agent unable to
    write a thing. The tool list has to say so.
    """

    tools = _read(
        ["github_create_issue", "github_list_issues"],
        {"github:*:read"},
    )

    assert tools["github_list_issues"].permitted is True

    assert tools["github_create_issue"].enabled is True
    assert tools["github_create_issue"].permitted is False


def test_granting_the_write_scope_makes_it_usable():
    tools = _read(
        ["github_create_issue"],
        {"github:*:read", "github:*:write"},
    )

    assert tools["github_create_issue"].permitted is True


def test_the_narrow_scope_is_enough():
    # Any ONE covering scope does it. A user who granted only
    # "github:issue:write" must not be told they still need something.
    tools = _read(["github_create_issue"], {"github:issue:write"})

    assert tools["github_create_issue"].permitted is True


def test_required_scopes_name_a_grant_that_would_work():
    tools = _read(["github_create_issue"], {"github:*:read"})

    required = tools["github_create_issue"].required_scopes

    assert required, "a blocked tool must say what would unblock it"

    # Every one of them genuinely works. This is what makes the
    # settings dialog's one-click grant trustworthy rather than a
    # plausible guess.
    for scope in required:
        assert _read(["github_create_issue"], {scope})[
            "github_create_issue"
        ].permitted is True


def test_no_grants_loaded_means_no_claim_is_made():
    # `None` is "not computed", NOT "nothing granted". Treating the two
    # the same would mark every tool in the product unusable the moment
    # a caller forgot to load the scopes.
    tools = _read(["github_create_issue"], None)

    assert tools["github_create_issue"].permitted is True


# ---------------------------------------------------------------------
# The property that matters
# ---------------------------------------------------------------------


def test_the_flag_agrees_with_the_gate_that_actually_runs():
    """
    The screen and the executor must never disagree.

    A UI that recomputes "is this allowed?" from its own copy of the
    rules is a second implementation of a security decision, and the
    two drift. This asserts the flag matches DatabaseScopePolicy - the
    object the executor itself calls - for every tool on the server.
    """

    granted = {"github:*:read", "slack:message:write"}

    names = sorted(TOOLS_BY_NAME)

    tools = _read(names, granted)

    policy = DatabaseScopePolicy(granted, set(names), "Agent")

    for name in names:
        assert tools[name].permitted is policy.permits(
            TOOLS_BY_NAME[name]
        ), name
