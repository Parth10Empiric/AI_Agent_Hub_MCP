from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.schemas import Operation, RiskLevel, ToolDefinition  # noqa: E402
from api.approvals import may_auto_approve  # noqa: E402
from api.db.models import AgentTool  # noqa: E402
from api.scopes import (  # noqa: E402
    default_scopes,
    parse_scope,
    scope_catalog,
    tools_permitted_by,
)
from api.services.agent_service import _tool_rows_to_read  # noqa: E402
from tests.tool_fixtures import build_tool_definitions  # noqa: E402

"""
Properties the whole tool surface must hold, whatever is on it.

Every test here is written against the LIVE tool set rather than a
fixed list, and none of them mentions a tool count. That is the point:
the question they answer is not "are there 161 tools?" but "does
adding or removing one still produce a coherent system?" - which is
the question that actually matters, because the tool set changes and
the invariants do not.

Where a test does name a tool, it names a PAIR and asserts a
relationship between them (trashing is milder than deleting), never an
absolute fact that a rename would silently invalidate.
"""


TOOLS = build_tool_definitions()
BY_NAME = {tool.name: tool for tool in TOOLS}
CATALOG = scope_catalog(TOOLS)


# ---------------------------------------------------------------------
# Classification reaches every tool
# ---------------------------------------------------------------------


def test_no_tool_falls_through_to_the_failclosed_default():
    """
    A tool the classifier cannot read is a tool nobody configured.

    The default is WRITE/MEDIUM, which is SAFE but wrong: a read gets
    an approval prompt it never needed, and the scope it demands
    ("github:compare_commits:write") is one no user would think to
    grant. When this fails, the fix is a name whose first word is a
    verb - or an entry in OPERATION_OVERRIDES.
    """

    unreadable = [
        tool.name
        for tool in TOOLS
        if tool.classification_source == "default"
    ]

    assert unreadable == []


def test_every_tool_is_named_for_its_service():
    # The namespace is stripped from the name to derive the resource,
    # so a tool not carrying its prefix derives a scope built from its
    # own service name - "slack:slack_send_message:write".
    wrong = [
        tool.name
        for tool in TOOLS
        if tool.namespace and not tool.name.startswith(tool.namespace)
    ]

    assert wrong == []


def test_every_tool_has_a_description():
    # The description is what the model reads to choose, AND what the
    # router indexes. A tool without one is invisible to both.
    assert [tool.name for tool in TOOLS if not tool.description] == []


def test_tool_names_are_unique_across_services():
    duplicates = [
        name
        for name, count in Counter(t.name for t in TOOLS).items()
        if count > 1
    ]

    assert duplicates == []


# ---------------------------------------------------------------------
# Derived scopes stay coherent
# ---------------------------------------------------------------------


def test_every_scope_is_well_formed():
    for scope in CATALOG:
        # Raises InvalidScope on anything the API would refuse to
        # store, so a tool cannot demand a permission no user can be
        # granted.
        parse_scope(scope)


def test_no_scope_names_an_unreadable_resource():
    """
    "unknown" means the classifier could not find a resource at all.

    Such a scope is unusable in both directions: it appears in the
    grant UI as a meaningless word, and granting it covers exactly one
    tool nobody can identify.
    """

    unreadable = [
        tool.name
        for tool in TOOLS
        if ":unknown:" in tool.permissions[0]
    ]

    assert unreadable == []


def test_no_scope_resource_starts_with_a_stray_preposition():
    """
    A resource like "to_channel" is a name that was split wrongly.

    slack_invite_to_channel derived "slack:to_channel:admin" - the
    verb ate "invite" and the resource kept the preposition. It is
    grammatical noise in a permission a human has to read and agree
    to, and the fix is always the tool's name.
    """

    stray = [
        (tool.name, tool.permissions[0])
        for tool in TOOLS
        if re.search(r":(to|from|or|and|the|a|in|on)_", tool.permissions[0])
    ]

    assert stray == []


def test_a_resource_scope_is_always_singular():
    """
    "github:release:read" and "github:releas:read" are two grants for
    one resource, and a user who holds the first is silently refused
    by a tool requiring the second.

    Caught by comparing every resource against the singular of itself:
    a correctly singularized word is its own singular.
    """

    from agent.text import singularize

    unstable = []

    for tool in TOOLS:
        resource = tool.permissions[0].split(":")[1]

        for word in resource.split("_"):
            if singularize(word) != word:
                unstable.append((tool.name, resource))
                break

    assert unstable == []


def test_the_catalogue_covers_every_tools_scopes():
    # scope_catalog is what the API validates a grant against. A scope
    # a tool requires but the catalogue omits is a permission the user
    # is refused permission to give.
    for tool in TOOLS:
        for scope in tool.permissions:
            assert scope in CATALOG, (tool.name, scope)


def test_a_new_agent_can_read_every_service_and_write_none():
    granted = default_scopes(TOOLS)

    permitted = tools_permitted_by(granted, TOOLS)

    assert permitted, "a new agent must be able to do something"

    # Reads only - the whole point of the default.
    assert all(tool.read_only for tool in permitted)

    # ...and every service is reachable, so a new agent is useful
    # everywhere rather than only where the alphabet put it.
    assert {tool.namespace for tool in permitted} == {
        tool.namespace for tool in TOOLS if tool.read_only
    }


# ---------------------------------------------------------------------
# Danger is graded, and the grading is used
# ---------------------------------------------------------------------


def test_everything_that_mutates_asks_a_human_first():
    unguarded = [
        tool.name
        for tool in TOOLS
        if not tool.read_only and not tool.requires_approval
    ]

    assert unguarded == []


def test_a_read_is_never_more_than_low_risk():
    # A read that rates MEDIUM or above means the classification is
    # confused about what the tool does - or the tool does more than
    # read and is named wrongly.
    overrated = [
        (tool.name, tool.risk_level.value)
        for tool in TOOLS
        if tool.read_only
        and tool.risk_level.severity > RiskLevel.LOW.severity
    ]

    assert overrated == []


def test_no_critical_tool_can_be_given_a_standing_yes():
    for tool in TOOLS:
        if tool.risk_level is RiskLevel.CRITICAL:
            assert not may_auto_approve(tool), tool.name


def test_the_irreversible_tools_are_the_critical_ones():
    """
    CRITICAL is not "important", it is "cannot be undone".

    Checked as a RELATIONSHIP rather than a list, so it survives
    renames: for each pair below, the destructive tool must outrank
    its recoverable sibling. Both halves matter - if trashing were
    also CRITICAL the distinction would be useless, and an agent would
    have no safer option to reach for.
    """

    pairs = [
        ("google_drive_delete_file", "google_drive_trash_file"),
        ("google_calendar_delete_calendar",
         "google_calendar_remove_subscription"),
        ("github_delete_repository", "github_delete_file"),
    ]

    for destructive, recoverable in pairs:
        worse = BY_NAME[destructive]
        milder = BY_NAME[recoverable]

        assert worse.risk_level is RiskLevel.CRITICAL, destructive

        assert milder.risk_level.severity < worse.risk_level.severity, (
            f"{recoverable} is recoverable and must rank below "
            f"{destructive}"
        )


def test_access_control_is_separated_from_ordinary_writes():
    """
    Sharing is not writing.

    A grant of "may write to GitHub" must not also mean "may hand my
    private repositories to other people", so anything that changes
    WHO CAN SEE something is ADMIN and asks for an admin scope.
    """

    sharing = [
        tool
        for tool in TOOLS
        if "collaborator" in tool.name
        or "permission" in tool.name
        or "acl" in tool.name
        or "channel_member" in tool.name
    ]

    assert sharing, "the fixture found no sharing tools at all"

    for tool in sharing:
        if tool.read_only:
            continue

        assert tool.operation is Operation.ADMIN, tool.name

        assert all(
            scope.endswith(":admin") for scope in tool.permissions
        ), tool.name


# ---------------------------------------------------------------------
# Adding and removing tools
# ---------------------------------------------------------------------


def test_a_brand_new_tool_needs_no_configuration_anywhere():
    """
    The flexibility claim, tested rather than asserted.

    A tool the server did not have a moment ago must arrive with an
    operation, a risk level and a pair of scopes, and must widen the
    catalogue - with no migration, no constant to update and no entry
    added to any list.
    """

    from agent.discovery import ToolDiscovery
    from tests.tool_fixtures import FakeToolsResult, extract_raw_tools

    invented = {
        "name": "github_list_deployments",
        "description": "List the deployments of a repository.",
        "inputSchema": {
            "type": "object",
            "properties": {"owner": {"type": "string"}},
            "required": ["owner"],
        },
    }

    grown = ToolDiscovery(
        server_name="personal-mcp-server"
    ).discover_from_result(
        FakeToolsResult(extract_raw_tools() + [invented])
    )

    fresh = {tool.name: tool for tool in grown}["github_list_deployments"]

    assert fresh.operation is Operation.READ
    assert fresh.classification_source != "default"
    assert fresh.permissions == (
        "github:deployment:read",
        "github:*:read",
    )

    # And the grant vocabulary grew to match, on its own.
    assert "github:deployment:read" in scope_catalog(grown)
    assert "github:deployment:read" not in CATALOG


def test_a_removed_tool_is_shown_as_unavailable_not_hidden():
    """
    Deleting a tool from the server must not silently delete a user's
    setting for it.

    Hiding the row makes a ticked checkbox vanish with no explanation
    and leaves a dead row in the database forever. Marking it
    unavailable lets the screen say so and lets the user clear it.
    """

    row = AgentTool(
        agent_id=None,
        tool_name="github_a_tool_that_was_removed",
        namespace="github",
        enabled=True,
        requires_approval=False,
    )

    [read] = _tool_rows_to_read([row], BY_NAME, granted=None)

    assert read.available is False
    assert read.tool_name == "github_a_tool_that_was_removed"

    # The classification fields are absent rather than invented.
    assert read.operation is None
    assert read.risk_level is None


def test_the_frontend_payload_is_complete_for_every_tool():
    """
    Everything ToolPicker renders must actually arrive.

    The picker groups by namespace and operation, badges by risk, and
    shows "Asks first" from requires_approval. A tool missing any of
    them renders in an "undefined" group or with no badge at all.
    """

    rows = [
        AgentTool(
            agent_id=None,
            tool_name=tool.name,
            namespace=tool.namespace or "",
            enabled=tool.read_only,
            requires_approval=tool.requires_approval,
        )
        for tool in TOOLS
    ]

    reads = _tool_rows_to_read(rows, BY_NAME, granted=default_scopes(TOOLS))

    assert len(reads) == len(TOOLS)

    for read in reads:
        assert read.available
        assert read.namespace
        assert read.description
        assert read.operation in {"read", "write", "delete", "admin"}
        assert read.risk_level in {
            "safe",
            "low",
            "medium",
            "high",
            "critical",
        }


def test_default_scopes_permit_the_tools_the_picker_pre_ticks():
    """
    The create wizard ticks reads by default and the API grants read
    scopes by default. If those two defaults disagree, a brand-new
    agent opens with tools ticked that it is not allowed to run -
    which is the exact confusion `permitted` was added to end.
    """

    granted = default_scopes(TOOLS)

    rows = [
        AgentTool(
            agent_id=None,
            tool_name=tool.name,
            namespace=tool.namespace or "",
            enabled=tool.read_only,
            requires_approval=tool.requires_approval,
        )
        for tool in TOOLS
        if tool.read_only
    ]

    for read in _tool_rows_to_read(rows, BY_NAME, granted):
        assert read.permitted, read.tool_name


# ---------------------------------------------------------------------
# Hand-maintained tables must not fall behind the tools
# ---------------------------------------------------------------------


def test_no_tuning_table_names_a_tool_that_does_not_exist():
    """
    The three hand-written tables are the only places a tool is named
    by hand, so they are the only places a rename can rot.

    A stale entry is silent in the worst way. An override for a
    deleted tool changes nothing and looks like it does; a keyword
    list for a renamed tool leaves the NEW name with no routing
    vocabulary while the old entry sits there looking maintained.
    Neither shows up as an error anywhere else.
    """

    from agent.classification import OPERATION_OVERRIDES, RISK_OVERRIDES
    from agent.lexicon import TOOL_KEYWORDS

    live = set(BY_NAME)

    for label, table in [
        ("OPERATION_OVERRIDES", OPERATION_OVERRIDES),
        ("RISK_OVERRIDES", RISK_OVERRIDES),
        ("TOOL_KEYWORDS", TOOL_KEYWORDS),
    ]:
        stale = sorted(set(table) - live)

        assert not stale, f"{label} names missing tools: {stale}"


def test_stopwords_never_swallow_an_intent_verb():
    """
    A stopword is dropped before scoring. An intent verb is the whole
    signal for whether the user wants to read or destroy something.

    A word in both lists is silently deleted from the query, and the
    router stops being able to tell "show the file" from "delete the
    file" - which is the one distinction that decides whether a
    destructive tool is even offered.
    """

    from agent.lexicon import ALL_INTENT_VERBS, STOPWORDS

    assert not (STOPWORDS & ALL_INTENT_VERBS)
