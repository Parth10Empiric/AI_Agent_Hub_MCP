from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.lexicon import ALL_INTENT_VERBS, STOPWORDS  # noqa: E402
from agent.text import fuzzy_ratio  # noqa: E402
from tests.tool_fixtures import build_router  # noqa: E402

"""
A confirmation must not change the subject.

THE SESSION THIS COMES FROM

    user   "list out all github repo naem"
    agent  lists two real repositories. github_list_repositories
           called, 592ms, succeeded.

    user   "delete test repo"
    agent  explains the deletion is permanent and asks for confirmation.

    user   "Yes, delete it"
    agent  "I don't actually have access to GitHub tools. Looking back
            at our conversation, I realize I made up those tools."

It had not made them up. It had called one, two messages earlier.

WHAT ACTUALLY HAPPENED

    "yes" -> "yesterday"    0.94

"yesterday" is a Google Calendar alias, and fuzzy_ratio scores a
three-letter prefix at 0.94 - all but an exact match. So the message
scored google_calendar at 0.940 and scored nothing else. Every GitHub
tool was filtered out before the model saw the turn.

The previous-turn carry-over did not save it either, and that is the
second half of the bug: `route` only carries the last turn's services
forward when the current message scores NO namespace. This message
scored one. Confidently. The wrong one.

So the model was handed Calendar tools, a transcript full of GitHub
work, and a request to delete a repository. Disowning its own history
was the only reading that made all three consistent.

WHY THE FIX IS A STOPWORD AND NOT A THRESHOLD

The obvious repair - refuse short prefixes of long words - breaks the
abbreviations the prefix rule exists for: "doc" for "document", "cal"
for "calendar", "org" for "organization" are all three letters and all
correct. Tried, measured, reverted.

A confirmation carries no topic BY DEFINITION: it inherits the topic of
the question it answers. That makes it grammar, which is what STOPWORDS
is for - and "no" was already in that list while "yes" was not.
"""


AFFIRMATIVES = (
    "yes", "yeah", "yep", "yup", "yah", "ya",
    "sure", "agreed", "absolutely", "definitely", "affirmative",
)


def test_yes_no_longer_reads_as_yesterday():
    """The collision itself, at the lowest level."""

    # Unchanged: this is still a 0.94 prefix match, and it must be -
    # "cal" for "calendar" is the rule working correctly.
    assert fuzzy_ratio("cal", "calendar") > 0.9

    # The repair is that "yes" never reaches the comparison, not that
    # the comparison changed.
    assert "yes" in STOPWORDS


def test_every_affirmative_is_a_stopword():
    for word in AFFIRMATIVES:
        assert word in STOPWORDS, word


def test_no_affirmative_is_an_intent_verb():
    """
    The rule the stopword list must never break.

    A stopword is dropped before routing. Dropping a verb that names an
    action would make the request unroutable, which is a worse bug than
    the one being fixed.
    """

    for word in AFFIRMATIVES:
        assert word not in ALL_INTENT_VERBS, word


def test_a_confirmation_stays_on_the_previous_service():
    """
    THE REGRESSION TEST.

    Exactly the three turns from the session, in order.
    """

    router = build_router()

    first = router.route("list out all github repo naem")

    assert first.namespaces == ("github",)

    second = router.route(
        "delete test repo",
        previous_namespaces=first.namespaces,
    )

    assert second.namespaces == ("github",)

    third = router.route(
        "Yes, delete it",
        previous_namespaces=second.namespaces,
    )

    assert third.namespaces == ("github",), third.namespaces

    # Not merely the right service - the right tool, at the top. The
    # user confirmed a deletion, and the tool that performs it has to
    # be in front of the model.
    names = [candidate.tool_name for candidate in third.candidates]

    assert "github_delete_repository" in names, names


def test_a_bare_yes_does_not_route_to_calendar():
    """
    The narrowest form, which is what most users actually type.

    With nothing but a stopword left, the message names no service at
    all - so the carry-over path in `route` takes over, which is the
    branch it was written for.
    """

    router = build_router()

    for word in ("yes", "yeah do it", "sure, go ahead", "yep"):

        decision = router.route(word, previous_namespaces=("github",))

        assert "google_calendar" not in decision.namespaces, (
            word,
            decision.namespaces,
        )

        assert decision.namespaces == ("github",), (word, decision.namespaces)


def test_a_confirmation_still_defers_to_a_named_service():
    """
    Carry-over must not become a pin.

    "yes, now check slack" names a service, and the previous turn does
    not get to override it - otherwise a conversation would never be
    able to change subject once it started.
    """

    router = build_router()

    decision = router.route(
        "yes, now check slack",
        previous_namespaces=("github",),
    )

    assert "slack" in decision.namespaces, decision.namespaces
