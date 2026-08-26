from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.policies import DatabaseScopePolicy  # noqa: E402
from api.services.chat_service import (  # noqa: E402
    BLOCKED_NOTICE,
    _blocked_capabilities,
)
from tests.tool_fixtures import build_router  # noqa: E402

"""
A permission system has to be able to say NO out loud.

THE SESSION THIS COMES FROM

    user   "list out all github repo name"

    agent  "Unfortunately, I don't have a tool available that can list
            all GitHub repositories... My available GitHub tools can
            only operate on repositories that are already named
            (github_list_workflow_runs, github_star_repository,
            github_update_repository, github_create_repository) - none
            of them can enumerate the list of repos under your
            account."

Every word of that was true from where the model was standing, and it
sent the user off to debug the router. The router was fine:
`github_list_repositories` ranked THIRD for that sentence.

The agent's granted scopes were:

    github:*:admin
    github:authenticated_user:read
    github:repository:write
    github:workflow_run:read

`github_list_repositories` requires `github:repository:read` or
`github:*:read`. Neither was granted - so the tool was removed before
the model ever saw it, and the model, having no way to know it existed,
explained its absence the only way it could: as a missing feature.

    the truth               "you have not granted me repository reads"
    what the user heard     "this product cannot list repositories"

That gap is the bug. Silently removing a capability and letting the
model improvise the reason is the worst of both worlds: the user is not
told what to fix, and they are told something false about the product.

Note what this does NOT change: the tool stays unavailable. Naming a
locked door is not a key.
"""


class Engine:
    """Just enough AgentEngine for the function under test."""

    def __init__(self) -> None:
        self.router = build_router()

    def route(self, query, **kwargs):
        return self.router.route(query, **kwargs)

    def get_tool(self, name):
        return next(
            (
                tool
                for tool in self.router.index.tools
                if tool.name == name
            ),
            None,
        )


# The real scopes from the failing agent.
JTEST_SCOPES = {
    "github:*:admin",
    "github:authenticated_user:read",
    "github:repository:write",
    "github:workflow_run:read",
    "slack:*:read",
    "slack:message:write",
}

QUERY = "list out all github repo name"


def blocked_for(scopes: set[str], enabled: set[str] | None = None):
    engine = Engine()

    all_tools = {tool.name for tool in engine.router.index.tools}
    enabled = all_tools if enabled is None else enabled

    policy = DatabaseScopePolicy(scopes, enabled, "Jtest")

    decision = engine.route(
        QUERY,
        allow=lambda definition: policy.permits(definition),
    )

    reported = asyncio.run(
        _blocked_capabilities(
            engine,
            QUERY,
            (),
            policy,
            enabled,
            decision,
        )
    )

    return decision, reported


def test_the_missing_tool_is_reported():
    _, reported = blocked_for(JTEST_SCOPES)

    assert any("github_list_repositories" in line for line in reported)


def test_it_names_the_permission_to_grant():
    # "You need a permission" is not actionable. "You need
    # github:repository:read" is one click.
    _, reported = blocked_for(JTEST_SCOPES)

    line = next(
        line for line in reported
        if "github_list_repositories" in line
    )

    assert "github:repository:read" in line
    assert "github:*:read" in line


def test_the_tool_is_still_not_offered():
    # The notice explains the absence. It must not end the absence -
    # this is transparency, not a permission grant.
    decision, reported = blocked_for(JTEST_SCOPES)

    offered = {candidate.tool_name for candidate in decision.candidates}

    assert "github_list_repositories" not in offered
    assert reported


def test_a_correctly_scoped_agent_gets_no_notice():
    # An agent that can do what was asked must not be handed a list of
    # things it cannot do. A notice on every turn is a notice the model
    # learns to skip.
    _, reported = blocked_for({"github:*:read", "slack:*:read"})

    assert reported == []


def test_a_switched_off_tool_says_so_differently():
    # Two gates, two sentences. "You turned this off" is one click in
    # the tool list; "you never granted this scope" is a different
    # page. Telling someone the wrong one wastes their afternoon.
    engine = Engine()

    all_tools = {tool.name for tool in engine.router.index.tools}

    _, reported = blocked_for(
        {"github:*:read", "slack:*:read"},
        enabled=all_tools - {"github_list_repositories"},
    )

    line = next(
        line for line in reported
        if "github_list_repositories" in line
    )

    assert "switched off" in line


def test_only_tools_that_would_have_been_offered_are_named():
    # Relevance is defined as "this would have made the cut if it were
    # permitted", measured by the same ranking that chose the others.
    # A read-only agent must not be lectured about every write tool on
    # the server.
    _, reported = blocked_for({"github:*:read", "slack:*:read"})

    assert not any("create" in line or "delete" in line for line in reported)


def test_the_notice_forbids_the_sentence_that_caused_this():
    lowered = BLOCKED_NOTICE.lower()

    assert "does not exist" in lowered
    assert "no such tool exists" in lowered


def test_the_notice_forbids_inventing_the_data_too():
    """
    Constraining the EXCUSE is not enough - it leaves the model free to
    do something worse.

        user   "so you just listout all folder name"
        agent  five folder names, five Drive-shaped ids, dates, a
               total. All invented, no tool called, and the next turn
               claimed the tool HAD been called.

    Nothing in the old notice was disobeyed: it never said "do not make
    the data up". A model with a direct request, no tool and an
    insisting user has exactly one completion that satisfies everybody,
    and it is the false one - so refusing has to be the instructed
    behaviour, not the assumed one.
    """

    lowered = BLOCKED_NOTICE.lower()

    assert "no data" in lowered
    assert "do not produce it" in lowered

    # The specific shapes that were fabricated in that session. A
    # blanket "be accurate" is what the model already believed it was
    # being.
    for shape in ("names", "identifiers", "counts", "dates", "lists"):
        assert shape in lowered, shape


def test_a_service_the_agent_does_not_have_says_which_screen():
    """
    THE THIRD REASON, which used to be reported as the second.

    A tool with no agent_tools row at all was described as "switched off
    in this agent's tool settings" - sending the user to a page whose
    per-tool switches had been removed. No permission grant fixes it
    either. The fix is one screen back: add the service to the agent.
    """

    engine = Engine()

    github = {
        tool.name
        for tool in engine.router.index.tools
        if tool.namespace == "github"
    }

    # A GitHub-only agent - exactly the shape that could not reach
    # Google Drive however many Drive permissions it was granted.
    _, reported = blocked_for(
        {"github:*:read", "google_drive:*:read"},
        enabled=github - {"github_list_repositories"},
    )

    line = next(
        line for line in reported
        if "github_list_repositories" in line
    )

    # This one HAS a github row, just switched off - the other branch.
    assert "switched off" in line

    drive = blocked_for(
        {"google_drive:*:read"},
        enabled=github,
    )[1]

    for line in drive:
        if "google_drive" in line:
            assert "not added to this agent" in line, line
            assert "switched off" not in line, line
