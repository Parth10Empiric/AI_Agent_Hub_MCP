from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.schemas import Operation  # noqa: E402
from tests.tool_fixtures import build_router  # noqa: E402

"""
Tests for Phase 2.3 routing, against the real 61-tool set.

The Phase 2 milestone in Phase2.md is defined as five queries that must
route correctly. Those five are the first tests below, so "is Phase 2
done?" has a runnable answer rather than an opinion.
"""


ROUTER = build_router()


def route(query: str, **kwargs):
    return ROUTER.route(query, **kwargs)


def top_tool(query: str) -> str:
    return route(query).candidates[0].tool_name


def selected(query: str) -> list[str]:
    return route(query).tool_names


def services(query: str) -> list[str]:
    return list(route(query).namespaces)


# ---------------------------------------------------------------------
# Phase 2 milestone tests (Phase2.md, "Phase 2 milestone")
# ---------------------------------------------------------------------


def test_milestone_1_github_issues():
    decision = route("List my open GitHub issues.")

    assert "github" in decision.namespaces
    assert "github_list_issues" in decision.tool_names
    assert not decision.fallback_used


def test_milestone_2_drive_documents():
    decision = route("Find my MCP documents in Drive.")

    assert "google_drive" in decision.namespaces
    assert decision.candidates[0].tool_name == (
        "google_drive_search_files"
    )


def test_milestone_3_slack_messages():
    decision = route(
        "Find messages in Slack about the GitHub deployment."
    )

    assert "slack" in decision.namespaces
    assert "slack_channel_history" in decision.tool_names


def test_milestone_4_calendar_events():
    decision = route("What meetings do I have tomorrow?")

    assert "google_calendar" in decision.namespaces
    assert decision.candidates[0].tool_name == (
        "google_calendar_list_events"
    )


def test_milestone_5_multi_service():
    # The hard one. A single request that legitimately spans two
    # services, and the case a single-winner router cannot serve at
    # all.
    decision = route(
        "Find the GitHub authentication issue and check whether "
        "anyone discussed it in Slack."
    )

    assert "github" in decision.namespaces
    assert "slack" in decision.namespaces

    grouped = decision.by_namespace()

    # Both services must actually contribute tools. Naming both
    # services but returning only GitHub tools would leave the agent
    # unable to check Slack.
    assert grouped.get("github"), "no GitHub tools selected"
    assert grouped.get("slack"), "no Slack tools selected"


# ---------------------------------------------------------------------
# Typo tolerance - the explicit requirement
# ---------------------------------------------------------------------


def test_misspelled_service_names_still_route():
    cases = [
        ("show me my githb issues", "github"),
        ("whats on my calender tomorow", "google_calendar"),
        ("gogle drve files", "google_drive"),
        ("send a slak mesage", "slack"),
    ]

    for query, expected in cases:
        assert expected in services(query), query


def test_misspelled_nouns_still_route():
    decision = route("show me my githb isues")

    assert "github" in decision.namespaces
    assert any(
        "issue" in name
        for name in decision.tool_names
    )


def test_typos_do_not_trigger_the_fallback():
    # A typo should route normally, not silently degrade into
    # "send everything and hope".
    assert not route("show me my githb isues").fallback_used


# ---------------------------------------------------------------------
# Synonyms - different words, related meaning
# ---------------------------------------------------------------------


def test_synonyms_route_without_naming_the_service():
    cases = [
        ("what is on my agenda today", "google_calendar"),
        ("am i free at 3pm", "google_calendar"),
        ("show me the open tickets", "github"),
        ("what did the team discuss yesterday", "slack"),
        ("find my spreadsheet", "google_drive"),
    ]

    for query, expected in cases:
        assert expected in services(query), query


def test_freebusy_is_reachable_by_natural_phrasing():
    # "freebusy" is a word no user will ever type. The keyword list in
    # agent/lexicon.py is what connects "am i free" to it.
    #
    # This asserts the property that actually matters - the lexicon
    # made the connection - rather than an exact rank. freebusy needs
    # three arguments the query does not supply, so the executability
    # signal legitimately puts it neck-and-neck with
    # google_calendar_list_events, which can answer the same question
    # with no arguments at all. Both are offered to the model, and
    # pinning an exact #1 between two equally valid tools would be
    # over-fitting the test to a 0.01 score difference.
    decision = route("am i free tomorrow afternoon")

    assert "google_calendar_freebusy" in decision.tool_names

    ranked = decision.tool_names.index("google_calendar_freebusy")
    assert ranked < 3, "freebusy should be near the top"

    best_lexical = max(
        decision.candidates,
        key=lambda c: c.breakdown.lexical,
    )
    assert best_lexical.tool_name == "google_calendar_freebusy", (
        "the keyword lexicon should make freebusy the strongest "
        "textual match for this phrasing"
    )


# ---------------------------------------------------------------------
# Intent and safety
# ---------------------------------------------------------------------


def test_intent_detection():
    assert route("show me my issues").intent is Operation.READ
    assert route("create an issue").intent is Operation.WRITE
    assert route("delete that event").intent is Operation.DELETE


def test_leading_verb_wins_over_later_lookalikes():
    # "open" is a write verb, but "show" comes first and is what the
    # user actually asked for.
    assert route("show me my open issues").intent is Operation.READ


def test_read_requests_never_surface_destructive_tools():
    # The safety rule. Deleting and re-sharing cannot be undone, so a
    # read-shaped request must not put them in front of the model.
    decision = route("show me my drive files")

    assert decision.intent is Operation.READ

    for candidate in decision.candidates:
        assert candidate.tool.operation not in (
            Operation.DELETE,
            Operation.ADMIN,
        ), candidate.tool_name


def test_write_requests_still_receive_read_tools():
    # The case that is easy to get wrong. To send a message to John,
    # the agent must first call slack_list_users to turn "John" into a
    # user id. Stripping reads from write requests breaks every
    # realistic multi-step task.
    decision = route("send a slack message to john")

    assert decision.intent is Operation.WRITE
    assert "slack_send_message" in decision.tool_names

    assert any(
        candidate.tool.read_only
        for candidate in decision.candidates
    ), "a write request must still expose lookup tools"


def test_a_read_shaped_question_about_writes_still_surfaces_them():
    """
    The bug that made the agent lie about what it could do.

    "List all GitHub tools available to you for write or update
    operations" leads with "list", so intent detection called it a
    READ - and operation_alignment scored every write tool at a hard
    0.0. All eight slots filled with `list_*` and `search_*` reads,
    the model was handed no write tool at all, and answered - honestly,
    from what it could see - that it had none. The user had four.

    The first verb is still the intent. What changed is that a kind of
    action the user NAMED is no longer erased by it.
    """

    decision = route(
        "List all GitHub tools available to you for write or "
        "update operations"
    )

    # The intent is unchanged - this really is a read-shaped sentence.
    assert decision.intent is Operation.READ

    writes = [
        candidate.tool_name
        for candidate in decision.candidates
        if candidate.tool.operation is Operation.WRITE
    ]

    assert writes, (
        "a question that says 'write' must surface write tools; "
        f"got {decision.tool_names}"
    )


def test_naming_two_kinds_of_action_surfaces_both():
    # "access" is an ADMIN verb and comes first, so it won the intent.
    # Both of the things the user actually asked about have to appear
    # regardless of which verb happened to lead the sentence.
    decision = route("do you have access to create or read issues")

    operations = {
        candidate.tool.operation
        for candidate in decision.candidates
    }

    assert Operation.WRITE in operations
    assert Operation.READ in operations


def test_an_unnamed_action_is_still_kept_out_of_read_requests():
    # The other half of the rule, and the one that matters for safety.
    # "show me my drive files" names only reads, so the exception never
    # fires and delete stays hidden - exactly as before.
    decision = route("show me my drive files")

    assert decision.intent is Operation.READ

    for candidate in decision.candidates:
        assert candidate.tool.operation is not Operation.DELETE, (
            candidate.tool_name
        )


# ---------------------------------------------------------------------
# Multi-service, multi-step requests
# ---------------------------------------------------------------------
#
# The hardest thing a router is asked to do, and the shape of every
# useful agent task: read something here, write something there, then
# report it somewhere else.
#
# The query is a real one, verbatim, from the session that exposed all
# three bugs below.


MULTI_STEP = (
    "Inspect the **Empira_HR** repo and find exactly **3 real, verifiable "
    "issues**. Create each issue in the repo with a short title, problem, "
    "file/location, impact, and brief fix suggestion. Do not modify code or "
    "invent issues. After creating all 3, send a short summary with the issue "
    "titles and links to **#mcp_test** on Slack. Use minimum tokens and avoid "
    "unnecessary exploration."
)


def github_and_slack(tool) -> bool:
    """
    An agent permitted on GitHub and Slack, and nothing else.

    The production path always passes a predicate like this - it is the
    agent's own permission policy - so routing is tested the way it is
    actually called. See chat_service.send_message.
    """

    return tool.namespace in {"github", "slack"}


def test_each_service_gets_a_way_to_do_what_was_asked():
    """
    A write quota shared BETWEEN services is won by one of them.

    GitHub scores higher than Slack on this query, so it took every
    reserved write slot. Slack was left with its three best-scoring
    reads - list_users, get_user, auth_info - and the agent reported,
    correctly, that it had no way to post anything. The second half of
    the task was impossible before the model saw a single token.
    """

    names = route(MULTI_STEP, allow=github_and_slack).tool_names

    assert "github_create_issue" in names, names
    assert "slack_send_message" in names, names


def test_a_write_tool_comes_with_the_lookup_that_addresses_it():
    """
    slack_send_message takes a channel_id, and nobody types one.

    Offering the write without a way to turn "#mcp_test" into an id is
    offering a tool the model cannot call - it either invents an id or
    gives up, and both look like the agent being broken.
    """

    names = route(MULTI_STEP, allow=github_and_slack).tool_names

    assert "slack_send_message" in names, names
    assert "slack_list_channels" in names, names


def test_a_caller_predicate_keeps_the_budget_on_usable_tools():
    """
    The budget must not be spent on tools that will be thrown away.

    This query pools Google Drive on the word "file". An agent with no
    Drive permission had six of its sixteen slots filled with Drive
    tools, all of which the scope gate then discarded - so the model
    received ten tools where the budget allowed sixteen, and the ones
    it lost were ones it needed.
    """

    unfiltered = route(MULTI_STEP).tool_names

    assert any(n.startswith("google_drive_") for n in unfiltered), (
        "this query is only interesting because it pools Drive"
    )

    filtered = route(MULTI_STEP, allow=github_and_slack).tool_names

    assert not any(n.startswith("google_drive_") for n in filtered)

    # The freed slots went somewhere useful rather than being lost.
    assert len(filtered) >= len(unfiltered)


def test_a_reserved_tool_is_never_trimmed_away_again():
    # The reserve passes used to overfill and then cut the result back
    # by score, which always discarded the lowest-scoring picks - the
    # exact tools the reservations existed to protect.
    decision = route(MULTI_STEP, allow=github_and_slack)

    assert len(decision.candidates) <= 24

    scores = [c.score for c in decision.candidates]

    assert scores == sorted(scores, reverse=True)


def test_delete_requests_do_surface_delete_tools():
    decision = route("delete the meeting tomorrow")

    assert decision.intent is Operation.DELETE
    assert "google_calendar_delete_event" in decision.tool_names


# ---------------------------------------------------------------------
# Selection behaviour
# ---------------------------------------------------------------------


def test_routing_sends_far_fewer_than_all_tools():
    decision = route("List my open GitHub issues.")

    assert len(decision.candidates) <= 10
    assert len(decision.candidates) < 61


def test_top_k_is_respected():
    decision = route("List my open GitHub issues.", top_k=3)
    assert len(decision.candidates) <= 3


def test_candidates_are_sorted_by_score():
    scores = [
        candidate.score
        for candidate in route("show me my github issues").candidates
    ]
    assert scores == sorted(scores, reverse=True)


def test_weak_candidates_are_cut_by_the_relative_floor():
    # The floor is relative, not absolute: every returned tool must be
    # competitive with the best one. This is what keeps a confident
    # match from dragging along a tail of barely-related tools.
    from agent.router import RELATIVE_SCORE_FLOOR

    for query in [
        "show me my github issues",
        "am i free tomorrow",
        "send a slack message to john",
    ]:
        decision = route(query)

        assert not decision.fallback_used, query

        best = decision.candidates[0].score

        for candidate in decision.candidates:
            assert candidate.score >= best * RELATIVE_SCORE_FLOOR, (
                f"{query}: {candidate.tool_name} is below the floor"
            )


# ---------------------------------------------------------------------
# Conversation context
# ---------------------------------------------------------------------


def test_followup_without_a_service_uses_previous_context():
    # Real conversations are full of these. Routed alone, "show me
    # more" matches nothing and falls back to everything.
    decision = route(
        "show me more",
        previous_namespaces=("github",),
    )
    assert "github" in decision.namespaces


def test_explicit_service_overrides_carried_context():
    # Context is a fallback, never an override. If the user names a
    # service, that wins.
    decision = route(
        "what meetings do i have tomorrow",
        previous_namespaces=("github",),
    )
    assert "google_calendar" in decision.namespaces
    assert "github" not in decision.namespaces


# ---------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------


def test_nonsense_falls_back_instead_of_returning_nothing():
    # Returning nothing is the worst outcome: the LLM cannot call a
    # tool it was never told about, so it hallucinates or gives up.
    # Uncertainty must resolve towards breadth.
    decision = route("qwertyuiop zxcvbnm")

    assert decision.fallback_used
    assert decision.candidates, "fallback must never return zero tools"


def test_fallback_is_flagged_not_silent():
    decision = route("qwertyuiop zxcvbnm")
    assert decision.confidence == 0.0
    assert decision.fallback_used is True


# ---------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------


def test_decisions_explain_themselves():
    decision = route("List my open GitHub issues.")
    candidate = decision.candidates[0]

    assert candidate.reasons
    assert candidate.breakdown.lexical > 0.0
    assert candidate.breakdown.namespace > 0.0
    assert decision.duration_ms >= 0.0


def test_decision_serializes_for_logging_and_the_ui():
    payload = route("List my open GitHub issues.").to_dict()

    assert payload["namespaces"] == ["github"]
    assert payload["intent"] == "read"
    assert payload["candidates"]


def test_approval_gated_tools_are_surfaced_up_front():
    decision = route("create an issue about the login bug")
    assert "github_create_issue" in decision.requires_approval()


# ---------------------------------------------------------------------
# Index lifecycle
# ---------------------------------------------------------------------


def test_index_rebuilds_when_the_registry_changes():
    router = build_router()

    before = router.index
    assert router.index is before, "index should be cached"

    router.registry.remove("github_list_issues")

    assert router.index is not before, (
        "index must rebuild after a registry mutation"
    )


def test_routing_is_fast_enough_for_the_request_path():
    # Routing runs on every user message, before the LLM is called.
    # A slow router is felt as a slow agent.
    decision = route("Find the GitHub issue discussed in Slack")
    assert decision.duration_ms < 100.0
