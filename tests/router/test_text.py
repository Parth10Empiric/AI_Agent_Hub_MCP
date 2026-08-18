from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.text import (  # noqa: E402
    damerau_levenshtein,
    edit_budget,
    fuzzy_ratio,
    normalize_text,
    singularize,
    split_identifier,
    tokenize,
)

"""
Unit tests for the text primitives.

These run with no MCP server, no LLM and no network, which is exactly
why the text layer was kept pure. When a query mis-routes in
production, the first thing you do is come here and add the failing
word pair as a test.
"""


# ---------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------


def test_normalize_lowercases_and_strips_punctuation():
    assert normalize_text("What's on my Calendar?") == (
        "what s on my calendar"
    )


def test_normalize_strips_accents():
    assert normalize_text("reunión") == "reunion"


def test_normalize_handles_none_and_empty():
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


def test_split_identifier_snake_case():
    assert split_identifier("github_list_repositories") == [
        "github",
        "list",
        "repositories",
    ]


def test_split_identifier_camel_case():
    assert split_identifier("githubListRepositories") == [
        "github",
        "list",
        "repositories",
    ]


def test_normalize_does_not_split_brand_words():
    # "GitHub" must survive as one token. Splitting it into
    # "git hub" would destroy the strongest routing signal we have.
    assert "github" in tokenize("Get the GitHub account")


# ---------------------------------------------------------------------
# Singularization
# ---------------------------------------------------------------------


def test_singularize_common_plurals():
    assert singularize("issues") == "issue"
    assert singularize("commits") == "commit"
    assert singularize("files") == "file"


def test_singularize_ies_plural():
    assert singularize("repositories") == "repository"


def test_singularize_ches_plural():
    assert singularize("branches") == "branch"


def test_singularize_protects_non_plurals():
    # These end in "s" but are not plural. Stripping the "s" would
    # corrupt the token and break every match against it.
    assert singularize("status") == "status"
    assert singularize("analysis") == "analysis"
    assert singularize("address") == "address"


# ---------------------------------------------------------------------
# Edit distance
# ---------------------------------------------------------------------


def test_edit_distance_basic_operations():
    assert damerau_levenshtein("github", "github") == 0
    assert damerau_levenshtein("githb", "github") == 1
    assert damerau_levenshtein("calender", "calendar") == 1


def test_edit_distance_counts_transposition_as_one():
    # This is the entire reason we use Damerau rather than plain
    # Levenshtein. Plain Levenshtein scores this as 2 and a swapped
    # pair of letters - the most common typo there is - falls outside
    # every sensible threshold.
    assert damerau_levenshtein("githbu", "github") == 1
    assert damerau_levenshtein("recieve", "receive") == 1


def test_edit_distance_respects_max_distance():
    # Returns budget + 1 as a sentinel rather than the true distance.
    assert damerau_levenshtein("abcdef", "uvwxyz", 2) == 3


def test_edit_budget_scales_with_length():
    assert edit_budget(4) == 0
    assert edit_budget(6) == 1
    assert edit_budget(10) == 2
    assert edit_budget(20) == 3


# ---------------------------------------------------------------------
# Fuzzy matching - the typo-tolerance contract
# ---------------------------------------------------------------------


def test_exact_match_scores_one():
    assert fuzzy_ratio("github", "github") == 1.0


def test_real_typos_match():
    for typo, correct in [
        ("githb", "github"),      # deletion
        ("githbu", "github"),     # transposition
        ("calender", "calendar"), # substitution
        ("gogle", "google"),
        ("drve", "drive"),
        ("slck", "slack"),
        ("mesage", "message"),
        ("chanel", "channel"),
    ]:
        assert fuzzy_ratio(typo, correct) >= 0.78, (
            f"{typo!r} should match {correct!r}"
        )


def test_abbreviations_match_by_prefix():
    assert fuzzy_ratio("repo", "repository") >= 0.9
    assert fuzzy_ratio("doc", "document") >= 0.9


def test_unrelated_short_words_do_not_match():
    # The failure this guards against is real: "chat" is a Slack alias
    # and "what" appears in most questions. One edit apart, completely
    # unrelated. Without a length-scaled budget, every question that
    # started with "what" pulled in Slack.
    assert fuzzy_ratio("what", "chat") == 0.0
    assert fuzzy_ratio("cat", "car") == 0.0
    assert fuzzy_ratio("meet", "meat") == 0.0


def test_short_tokens_require_exact_match():
    # "pr" and "gh" are real aliases, but two-letter fuzzy matching
    # would collide with half the dictionary.
    assert fuzzy_ratio("pr", "pr") == 1.0
    assert fuzzy_ratio("pr", "or") == 0.0


def test_unrelated_long_words_do_not_match():
    assert fuzzy_ratio("calendar", "repository") == 0.0
    assert fuzzy_ratio("slack", "github") == 0.0


def test_fuzzy_ratio_is_symmetric():
    assert fuzzy_ratio("githb", "github") == fuzzy_ratio(
        "github", "githb"
    )
