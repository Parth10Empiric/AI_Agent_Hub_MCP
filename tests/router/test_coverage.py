from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.executor import ToolExecutor  # noqa: E402
from agent.router import (  # noqa: E402
    BASE_TOP_K,
    MAX_TOP_K,
    MIN_TOOLS_PER_NAMESPACE,
    TOP_K_PER_EXTRA_SERVICE,
)
from tests.tool_fixtures import build_registry, build_router  # noqa: E402

"""
Regression tests for the four fixes that came out of a real session.

The query was:

    "check github drive slack and calander connection."

and three things went wrong:

  1. Google Calendar scored 0.80 (the user typed "calander") while the
     other three services scored 1.00. It missed the "primary" cutoff,
     received exactly ONE tool, and the agent could not check it.

  2. That one tool was google_calendar_freebusy, which needs three
     arguments nobody supplied - because the word "check" appears in
     its docstring and nowhere else in the corpus, so IDF treated a
     generic command verb as decisive evidence.

  3. google_drive_search_files failed on page_size='1' - the model sent
     a string where the schema wanted an integer.

Every test below locks in one of those fixes.
"""


ROUTER = build_router()
REGISTRY = build_registry()

FOUR_SERVICE_QUERY = "check github drive slack and calander connection."


# ---------------------------------------------------------------------
# Fix 1 - every selected service gets a usable set of tools
# ---------------------------------------------------------------------


def test_every_selected_service_gets_tools():
    decision = ROUTER.route(FOUR_SERVICE_QUERY)

    grouped = decision.by_namespace()

    for namespace in decision.namespaces:
        assert grouped.get(namespace), (
            f"{namespace} was selected but got no tools"
        )


def test_weaker_service_still_gets_its_quota():
    # Calendar scores lower than the rest purely because of the typo.
    # It must still receive a working set, not a single token tool.
    decision = ROUTER.route(FOUR_SERVICE_QUERY)

    calendar = decision.by_namespace().get("google_calendar", [])

    assert len(calendar) >= MIN_TOOLS_PER_NAMESPACE, (
        f"calendar got {len(calendar)} tools, expected at least "
        f"{MIN_TOOLS_PER_NAMESPACE}"
    )


def test_all_four_services_are_reachable_in_one_request():
    decision = ROUTER.route(FOUR_SERVICE_QUERY)

    assert set(decision.namespaces) == {
        "github",
        "google_drive",
        "slack",
        "google_calendar",
    }


# ---------------------------------------------------------------------
# Fix 2 - the tool budget scales with the number of services
# ---------------------------------------------------------------------


def test_single_service_query_stays_small():
    decision = ROUTER.route("show me my github issues")

    assert len(decision.namespaces) == 1
    assert len(decision.candidates) <= BASE_TOP_K


def test_multi_service_query_gets_a_bigger_budget():
    single = ROUTER.route("show me my github issues")
    multi = ROUTER.route(FOUR_SERVICE_QUERY)

    assert len(multi.candidates) > len(single.candidates), (
        "a four-service request needs more tools than a one-service one"
    )


def test_budget_formula_matches_the_constants():
    decision = ROUTER.route(FOUR_SERVICE_QUERY)

    expected = min(
        MAX_TOP_K,
        BASE_TOP_K
        + TOP_K_PER_EXTRA_SERVICE * (len(decision.namespaces) - 1),
    )

    assert len(decision.candidates) <= expected


def test_budget_never_exceeds_the_ceiling():
    # Past a point, more tools hurt the model more than they help.
    for query in [
        FOUR_SERVICE_QUERY,
        "github drive slack calendar issues files messages events "
        "repositories folders channels meetings commits branches",
    ]:
        assert len(ROUTER.route(query).candidates) <= MAX_TOP_K


# ---------------------------------------------------------------------
# Fix 3 - command verbs are grammar, not topic
# ---------------------------------------------------------------------


def test_a_command_verb_cannot_out_rank_the_subject():
    # "check" appears in exactly one docstring out of 61, so raw IDF
    # made it look decisive. It is a command word, not a topic.
    decision = ROUTER.route(FOUR_SERVICE_QUERY)

    top = decision.candidates[0]

    assert top.tool_name != "google_calendar_freebusy", (
        "a generic verb should not decide the top tool"
    )


def test_connection_check_prefers_tools_that_need_no_arguments():
    decision = ROUTER.route(FOUR_SERVICE_QUERY)

    # The point of the executability signal: for a bare "is it
    # connected?" question, the top few tools should be runnable
    # without the user supplying anything.
    for candidate in decision.candidates[:4]:
        required = candidate.tool.input_schema.get("required", [])
        assert not required, (
            f"{candidate.tool_name} needs {required} for a "
            "no-arguments question"
        )


def test_executability_prefers_the_runnable_tool_of_two_equals():
    freebusy = REGISTRY.get("google_calendar_freebusy")
    listing = REGISTRY.get("google_calendar_list_calendars")

    terms = {"check", "calendar", "connection"}

    assert ROUTER.executability(listing, terms) == 1.0
    assert ROUTER.executability(freebusy, terms) < 1.0


def test_executability_does_not_punish_supplied_arguments():
    # "get repository owner/repo" names what it needs, so the tool
    # should not be penalised for requiring it.
    tool = REGISTRY.get("github_get_repository")

    hinted = ROUTER.executability(tool, {"repo", "owner", "get"})
    blind = ROUTER.executability(tool, {"something", "else"})

    assert hinted == 1.0
    assert blind < hinted


def test_executability_is_a_tiebreaker_not_a_gate():
    # A tool with required arguments must still win when the topic
    # clearly matches it.
    decision = ROUTER.route("get the repository argus/test")

    assert "github_get_repository" in decision.tool_names


# ---------------------------------------------------------------------
# Fix 4 - argument type coercion
# ---------------------------------------------------------------------


def test_string_integer_is_coerced():
    # The exact failure from the session log.
    tool = REGISTRY.get("google_drive_search_files")

    coerced, notes = ToolExecutor.coerce_arguments(
        tool,
        {"query": "x", "page_size": "1"},
    )

    assert coerced["page_size"] == 1
    assert isinstance(coerced["page_size"], int)
    assert notes


def test_string_boolean_is_coerced():
    tool = REGISTRY.get("slack_list_channels")

    coerced, _ = ToolExecutor.coerce_arguments(
        tool,
        {"exclude_archived": "true", "limit": "50"},
    )

    assert coerced["exclude_archived"] is True
    assert coerced["limit"] == 50


def test_single_value_is_wrapped_for_an_array_field():
    tool = REGISTRY.get("google_calendar_freebusy")

    coerced, _ = ToolExecutor.coerce_arguments(
        tool,
        {
            "time_min": "a",
            "time_max": "b",
            "calendar_ids": "primary",
        },
    )

    assert coerced["calendar_ids"] == ["primary"]


def test_coercion_never_invents_meaning():
    # Representation may be fixed. Meaning may not.
    tool = REGISTRY.get("google_drive_search_files")

    coerced, notes = ToolExecutor.coerce_arguments(
        tool,
        {"query": "x", "page_size": "not a number"},
    )

    assert coerced["page_size"] == "not a number"
    assert notes == []


def test_correct_values_are_left_untouched():
    tool = REGISTRY.get("google_drive_search_files")

    original = {"query": "budget", "page_size": 20}
    coerced, notes = ToolExecutor.coerce_arguments(tool, original)

    assert coerced == original
    assert notes == []


def test_booleans_are_not_read_as_integers():
    # bool is a subclass of int in Python. Without an explicit guard,
    # True would slide through the integer branch.
    tool = REGISTRY.get("google_drive_search_files")

    coerced, _ = ToolExecutor.coerce_arguments(
        tool,
        {"query": "x", "page_size": True},
    )

    assert coerced["page_size"] is True


# ---------------------------------------------------------------------
# Fix 5 - second-chance retrieval
# ---------------------------------------------------------------------


def test_exclude_returns_a_different_tool_set():
    first = ROUTER.route("show me my github issues")

    second = ROUTER.route(
        "show me my github issues",
        exclude=set(first.tool_names),
    )

    assert second.candidates, "a second pass must still find tools"

    assert not set(second.tool_names) & set(first.tool_names), (
        "the second pass must not repeat the failed tools"
    )


def test_exclude_still_respects_the_service():
    first = ROUTER.route("show me my github issues")

    second = ROUTER.route(
        "show me my github issues",
        exclude=set(first.tool_names),
    )

    assert "github" in second.namespaces


def test_excluding_everything_falls_back_rather_than_returning_nothing():
    everything = {tool.name for tool in REGISTRY.all()}

    decision = ROUTER.route(
        "show me my github issues",
        exclude=everything,
    )

    # Better to widen than to hand the model an empty toolbox.
    assert decision.fallback_used


# ---------------------------------------------------------------------
# Fix 6 - a tool from another service must not hijack the request
# ---------------------------------------------------------------------


def test_named_service_beats_a_better_worded_outsider():
    # From a real session: "test drive connection" ranked
    # slack_auth_info FIRST, above every Google Drive tool, because
    # the words "test" and "connection" both appear in its text.
    decision = ROUTER.route("test drive connection")

    assert decision.candidates[0].namespace == "google_drive", (
        "the service the user named must win"
    )


def test_outsiders_are_kept_out_when_a_service_is_named():
    decision = ROUTER.route("test drive connection")

    for candidate in decision.candidates:
        assert candidate.namespace == "google_drive", (
            f"{candidate.tool_name} is not a Drive tool"
        )


def test_cross_service_still_works_when_no_service_is_named():
    # The penalty scales with namespace confidence, so a query that
    # names no service pays no penalty at all. "who am I?" must still
    # reach GitHub, Slack and Calendar together.
    decision = ROUTER.route("who am I?")

    assert not decision.namespaces, "no service should be detected"

    namespaces = {
        candidate.namespace
        for candidate in decision.candidates
    }

    assert len(namespaces) > 1, (
        "a service-less question must stay cross-service"
    )


def test_multi_service_requests_are_unaffected():
    # Both services are named, so neither is an "outsider".
    decision = ROUTER.route(
        "Find the GitHub issue and check whether anyone "
        "discussed it in Slack."
    )

    grouped = decision.by_namespace()

    assert grouped.get("github")
    assert grouped.get("slack")


# ---------------------------------------------------------------------
# Fix 7 - only escalate on failures a different tool could fix
# ---------------------------------------------------------------------


def test_escalatable_errors_are_the_models_fault_not_the_services():
    from agent.errors import ErrorCode
    from agent.loop import ESCALATABLE_ERRORS

    # A different tool might avoid these.
    for code in [
        ErrorCode.TOOL_NOT_FOUND,
        ErrorCode.TOOL_NOT_AVAILABLE,
        ErrorCode.INVALID_ARGUMENTS,
        ErrorCode.NOT_FOUND,
    ]:
        assert code in ESCALATABLE_ERRORS


def test_service_failures_never_trigger_escalation():
    from agent.errors import ErrorCode
    from agent.loop import ESCALATABLE_ERRORS

    # If the service itself is broken, every tool in it fails the same
    # way. Widening the tool set just burns rounds - which is exactly
    # what happened when Drive's OAuth flow timed out and the loop
    # escalated twice for nothing.
    for code in [
        ErrorCode.UNKNOWN,
        ErrorCode.SERVICE_ERROR,
        ErrorCode.AUTHENTICATION_FAILED,
        ErrorCode.AUTHORIZATION_FAILED,
        ErrorCode.PERMISSION_DENIED,
        ErrorCode.APPROVAL_DENIED,
        ErrorCode.TIMEOUT,
        ErrorCode.CONNECTION_ERROR,
        ErrorCode.RATE_LIMITED,
    ]:
        assert code not in ESCALATABLE_ERRORS, code
