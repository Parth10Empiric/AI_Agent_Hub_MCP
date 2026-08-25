from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.schemas import Operation  # noqa: E402
from tests.tool_fixtures import build_router  # noqa: E402

"""
Routing a request phrased as a GOAL rather than as a tool name.

THE QUERY THIS COMES FROM

    "Student-Faculty-ApplicationReview-System repo summury provide me"

Every scoring signal failed at once, and the breakdown said so:

    github_list_pull_requests  0.501  lex=0.275 op=0.5 sem=0.0
    github_list_repositories   0.466  lex=0.188 op=0.5 sem=0.0
    github_get_repository      0.449  lex=0.188 op=0.5 sem=0.0
    github_create_repository   0.449  lex=0.188 op=0.5 sem=0.0
    github_delete_repository   0.449  lex=0.188 op=0.5 sem=0.0
    ...eleven candidates inside 0.06
    github_get_readme          0.438  <- rank 12, cut by top_k=8

  sem=0.0    embeddings were never switched on in production
  op=0.5     "summury" is not a verb, so no intent was detected - and
             a delete tool was offered to a plainly read-only request
  lex tied   eleven tools contain "repo" and nothing else matched;
             the repository NAME contributed nothing at all

What decided the ranking was `executability` - a 0.06-weight
tiebreaker - so the model was handed eight ways to MANAGE repositories
and no way to READ one. It then guessed the tech stack from the
repository's name and told the user it had no tool for the job.

The fixes are independent and each is tested separately below, because
the failure was not one bug: it was every signal being silent at the
same time.
"""


def route(query: str, **kwargs):
    return build_router().route(query, **kwargs)


def selected(query: str, **kwargs) -> list[str]:
    return [
        candidate.tool_name
        for candidate in route(query, **kwargs).candidates
    ]


# ---------------------------------------------------------------------
# Task expansion: a word that names a plan, not a tool
# ---------------------------------------------------------------------


def test_a_repo_summary_gets_the_tools_that_read_a_repo():
    tools = selected("repo summary please")

    # The two tools that can actually answer the question.
    assert "github_get_readme" in tools
    assert "github_get_file" in tools


def test_the_reading_tools_outrank_the_managing_ones():
    # Not merely present - AHEAD. Presence at rank 12 is what the old
    # ranking had, and top_k cut it off.
    tools = selected("repo summary please")

    assert tools.index("github_get_readme") < tools.index(
        "github_list_repositories"
    )


def test_a_misspelled_task_word_still_expands():
    # "summury" is one edit from "summary". Every other stage of the
    # router has been typo-tolerant since Phase 2.3.
    tools = selected(
        "Student-Faculty-ApplicationReview-System repo summury provide me"
    )

    assert "github_get_readme" in tools


def test_a_tech_stack_question_reaches_the_files():
    tools = selected("what is the argus/test repo built with")

    assert any(
        name in tools
        for name in (
            "github_get_readme",
            "github_get_file",
            "github_list_languages",
        )
    )


def test_expansion_does_not_outrank_a_tool_the_user_named():
    # An implied term must never beat a typed one, or an inference
    # starts overruling a fact.
    tools = selected("list my repositories")

    assert tools[0] == "github_list_repositories"


# ---------------------------------------------------------------------
# Intent: typos, and the absence of a verb
# ---------------------------------------------------------------------


def test_a_misspelled_verb_still_sets_the_intent():
    assert route("summury of my github repo").intent is Operation.READ


def test_a_read_shaped_request_never_surfaces_a_delete_tool():
    # The specific tool the old router offered for this sentence.
    tools = selected(
        "Student-Faculty-ApplicationReview-System repo summury provide me"
    )

    assert "github_delete_repository" not in tools


def test_a_request_with_no_verb_at_all_still_hides_destructive_tools():
    # Silence is not consent. With no verb, alignment used to return a
    # flat 0.5 for everything - including deletion.
    tools = selected("my github repo")

    assert not any("delete" in name for name in tools)


def test_naming_the_action_still_reaches_it():
    # The rule above must not make deletion unreachable, only unasked.
    tools = selected("delete the old test repo on github")

    assert "github_delete_repository" in tools


# ---------------------------------------------------------------------
# Per-clause intent
# ---------------------------------------------------------------------


def test_each_clause_keeps_its_own_kind_of_action():
    # One sentence, two intents. A single sentence-level intent has to
    # pick one and be wrong about the other.
    tools = selected("list my repos and delete the stale branch")

    assert "github_list_repositories" in tools
    assert "github_delete_branch" in tools


def test_a_single_intent_sentence_is_unaffected():
    tools = selected("show me my open github issues")

    assert "github_list_issues" in tools
    assert not any("delete" in name for name in tools)


# ---------------------------------------------------------------------
# Every service keeps a way to look at something
# ---------------------------------------------------------------------


def test_a_write_request_still_gets_a_read_tool_per_service():
    # "post a slack summary OF MY GITHUB ISSUES" is one clause with one
    # verb and two services. Splitting the posting from the reading
    # needs a parser - so the floor stands behind the clause rule.
    tools = selected(
        "post a slack summary of my github issues "
        "and save notes in drive and add a calendar event"
    )

    assert "slack_send_message" in tools

    # The half that was missing entirely: something to summarise.
    assert any(
        name in tools
        for name in ("github_list_issues", "github_search_issues")
    )


def test_every_pooled_service_is_represented():
    decision = route(
        "post a slack summary of my github issues "
        "and save notes in drive and add a calendar event"
    )

    namespaces = {
        candidate.namespace
        for candidate in decision.candidates
    }

    assert {"github", "slack", "google_drive", "google_calendar"} <= namespaces


# ---------------------------------------------------------------------
# Chainable arguments
# ---------------------------------------------------------------------


def test_a_second_step_tool_is_not_penalised_for_its_arguments():
    # Nobody types a file path, so `github_get_file` was scored as if
    # the user had failed to supply one - demoting every tool that can
    # only run second, which is every tool a multi-step task needs.
    router = build_router()

    get_file = next(
        tool
        for tool in router.index.tools
        if tool.name == "github_get_file"
    )

    # "path" is chainable; "owner" and "repo" are not, and are unhinted
    # here, so this asserts the chainable one was skipped rather than
    # that the penalty is gone.
    assert router.executability(get_file, {"summary"}) > 0.0

    without_path = router.executability(get_file, {"owner", "repo"})

    assert without_path == 1.0


# ---------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------


def test_a_tied_field_is_not_reported_as_confident():
    # Confidence used to measure how HIGH the winner scored. The
    # question that matters is whether it WON.
    router = build_router()

    tied = router._separation([0.50, 0.49, 0.49, 0.48, 0.48])
    clear = router._separation([0.90, 0.40, 0.35, 0.30, 0.28])

    assert tied < 0.10
    assert clear > 0.50


def test_a_confident_query_stays_confident():
    decision = route("list my github issues")

    assert decision.fallback_used is False
    assert decision.confidence > 0.4


# ---------------------------------------------------------------------
# The fallback
# ---------------------------------------------------------------------


def test_the_fallback_keeps_the_ranking_it_computed():
    # Low confidence means "I am not sure which of these wins", not
    # "these scores are meaningless". The index-order fallback dropped
    # github_get_readme purely because it is declared late in tools.py.
    decision = route("hmm what about that thing from earlier")

    assert decision.fallback_used is True

    # Widening must never return FEWER options than confidence does.
    assert len(decision.candidates) >= 8


# ---------------------------------------------------------------------
# Proper nouns
# ---------------------------------------------------------------------


def test_a_repository_name_does_not_route_the_request():
    # "review" is contained in "applicationreview", and containment
    # scored 0.90 - all but an exact match - so every pull-request
    # review tool was lifted to the top of a request about summarising
    # a repository.
    tools = selected(
        "Student-Faculty-ApplicationReview-System repo summury provide me"
    )

    assert "github_list_pull_request_reviews" not in tools
