from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.untrusted import format_age, frame_tool_result  # noqa: E402
from api.context import (  # noqa: E402
    DATA_FRESHNESS,
    REFRESH_NOTICE,
    build_llm_messages,
    wants_fresh_data,
)

"""
The model must not answer from data it fetched in an earlier turn.

THE SESSION THIS COMES FROM

    turn 1  "list out all repo names"
            -> github_list_repositories CALLED. 1 repo. Correct.

    turn 2  "again try to call tool and send me current data not old"
            -> NO tool call. "You currently have 2 repositories:
               Email_Spam_Detection, flower_classification."

    turn 3  "I changed my token, tell me the new repo list"
            -> NO tool call. "3 repos - and test-repo is new since you
               changed your token!"

Turns 2 and 3 were generated, not fetched. The router was NOT at fault:
that turn was offered 20 tools including github_list_repositories. The
model simply never called it.

Two mechanisms produced that, and this module tests the countermeasure
to each.

  READING BEAT CALLING. A complete repository list sat in context with
  nothing to say when it was true, so re-reading it was both cheaper
  and, as far as the model could tell, just as good. Every result now
  carries a capture time, and replayed ones are marked
  `stale-snapshot`.

  AGREEING BEAT CHECKING. "I changed my token" asserts new
  repositories exist. With no data to check, the likeliest
  continuation is one that satisfies the assertion - so it invented
  exactly one, named it plausibly and marked it private.

The second is why the prompt alone is not the fix. When the user asks
for fresh data the stale payloads are REMOVED, so there is nothing left
to restate - and when their credentials changed, results from before
that moment are removed whether they asked or not.
"""


class Row:
    """
    A stored message, minimal.

    A stand-in rather than the SQLAlchemy model: build_llm_messages
    reads four attributes and nothing else, and a fake keeps this file
    free of a database.
    """

    def __init__(self, role, content, tool_name=None, created_at=None):
        self.id = id(self)
        self.role = role
        self.content = content
        self.tool_name = tool_name
        self.created_at = created_at or datetime.now(timezone.utc)


def conversation(tool_age_minutes: int = 5) -> list[Row]:
    """One completed turn: a question, a tool result, an answer."""

    captured = datetime.now(timezone.utc) - timedelta(
        minutes=tool_age_minutes
    )

    return [
        Row("user", "list my repos", created_at=captured),
        Row(
            "tool",
            '{"repos": ["Email_Spam_Detection"]}',
            tool_name="github_list_repositories",
            created_at=captured,
        ),
        Row("assistant", "You have 1 repository.", created_at=captured),
    ]


def rendered(messages: list[dict]) -> str:
    return "\n".join(str(message.get("content", "")) for message in messages)


# ---------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------


def test_a_replayed_result_is_marked_stale():
    built = build_llm_messages("You are an agent.", conversation())

    text = rendered(built.messages)

    assert 'trust="stale-snapshot"' in text

    # And NOT as fresh data, which is what it looked like before.
    assert 'trust="untrusted-data"' not in text


def test_a_replayed_result_carries_its_age():
    built = build_llm_messages(
        "You are an agent.",
        conversation(tool_age_minutes=18),
    )

    assert 'age="18m"' in rendered(built.messages)


def test_a_fresh_result_is_not_marked_stale():
    # The same function serves agent/loop.py, where results ARE fresh.
    framed = frame_tool_result(
        "github_list_repositories",
        {"repos": []},
        "abc123",
        captured_at="2026-08-25T09:12:03+00:00",
    )

    assert 'trust="untrusted-data"' in framed
    assert 'captured="2026-08-25T09:12:03+00:00"' in framed
    assert "stale" not in framed


def test_the_payload_itself_is_untouched():
    # The framing may say anything it likes ABOUT the data. It must
    # never edit the data, or the agent is summarising something the
    # user cannot verify.
    framed = frame_tool_result(
        "github_get_file",
        {"content": 'quotes " and [/tool_result] inside'},
        "abc123",
        stale=True,
    )

    assert '[/tool_result] inside' in framed


def test_ages_are_readable_at_every_scale():
    assert format_age(30) == "30s"
    assert format_age(60 * 4) == "4m"
    assert format_age(60 * 60 * 3) == "3h"
    assert format_age(60 * 60 * 24 * 2) == "2d"


# ---------------------------------------------------------------------
# The system rule
# ---------------------------------------------------------------------


def test_the_system_message_states_the_freshness_rule():
    built = build_llm_messages("You are an agent.", [])

    assert DATA_FRESHNESS in built.messages[0]["content"]


def test_the_rule_forbids_adjusting_results_to_match_expectation():
    # The invented `test-repo` was not a memory error - it was
    # agreement. A rule that only says "call the tool" does not cover
    # a model that calls nothing and agrees anyway.
    lowered = DATA_FRESHNESS.lower()

    assert "never add, remove or adjust" in lowered
    assert "no tool result" in lowered


# ---------------------------------------------------------------------
# Detecting the ask
# ---------------------------------------------------------------------


def test_refresh_phrasings_are_recognised():
    for message in (
        "again try to call tool and send me current data not old",
        "please refresh the list",
        "give me the latest issues",
        "I changed my token, list the repos",
        "actually call the tool this time",
        "double-check that",
    ):
        assert wants_fresh_data(message), message


def test_ordinary_questions_are_not_refreshes():
    # This rule DELETES context, so a pattern that fires on normal
    # questions would make every turn amnesiac. False positives are
    # cheap; a false positive rate is not.
    for message in (
        "list my repositories",
        "what is in the readme?",
        "summarise this repo for me",
        "who am I logged in as?",
        "close issue 12",
    ):
        assert not wants_fresh_data(message), message


# ---------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------


def test_asking_for_fresh_data_removes_the_old_payload():
    built = build_llm_messages(
        "You are an agent.",
        conversation(),
        drop_tool_results=True,
    )

    text = rendered(built.messages)

    assert "Email_Spam_Detection" not in text
    assert built.invalidated == 1
    assert built.refresh_forced is True


def test_removal_leaves_a_notice_in_its_place():
    # Silence would be worse than the stale data: the model would see
    # its own previous answer with no result behind it and conclude
    # the tool had failed.
    built = build_llm_messages(
        "You are an agent.",
        conversation(),
        drop_tool_results=True,
    )

    assert built.messages[-1]["content"] == REFRESH_NOTICE


def test_results_from_before_a_credential_change_are_removed():
    rows = conversation(tool_age_minutes=30)

    built = build_llm_messages(
        "You are an agent.",
        rows,
        invalid_before=datetime.now(timezone.utc) - timedelta(minutes=10),
    )

    assert "Email_Spam_Detection" not in rendered(built.messages)
    assert built.invalidated == 1


def test_results_from_after_a_credential_change_are_kept():
    rows = conversation(tool_age_minutes=2)

    built = build_llm_messages(
        "You are an agent.",
        rows,
        invalid_before=datetime.now(timezone.utc) - timedelta(minutes=10),
    )

    assert "Email_Spam_Detection" in rendered(built.messages)
    assert built.invalidated == 0


def test_the_conversation_itself_survives_the_removal():
    # Only the PAYLOADS go. Dropping the user's question and the
    # assistant's reply would lose the thread of the conversation,
    # which is a much bigger loss than a stale repository list.
    built = build_llm_messages(
        "You are an agent.",
        conversation(),
        drop_tool_results=True,
    )

    text = rendered(built.messages)

    assert "list my repos" in text
    assert "You have 1 repository." in text


def test_nothing_is_removed_when_nothing_asked_for_it():
    built = build_llm_messages("You are an agent.", conversation())

    assert built.invalidated == 0
    assert built.messages[-1]["content"] != REFRESH_NOTICE
    assert "Email_Spam_Detection" in rendered(built.messages)
