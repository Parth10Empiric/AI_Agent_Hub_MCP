from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.untrusted import SECURITY_PREAMBLE  # noqa: E402
from api.context import TOOL_AUTHORITY, build_llm_messages  # noqa: E402

"""
The model must not trust its own stale claims about what it can do.

THE SESSION THIS COMES FROM

    07:51  16 tools offered, no write scope. The agent answers,
           correctly, "I have no create_issue tool."
    08:31  the user grants github:issue:write and slack:message:write.
    08:31  16 tools offered INCLUDING github_create_issue.
           rounds=1, ZERO tool calls: "I still cannot do this. My
           toolset has not changed since the previous turn."

It had changed. The model believed five of its own earlier replies
over the tool schema in front of it, and every retry added another
refusal for the next turn to agree with.

Permissions are the thing this product exists to let people change, so
a tool list that changes mid-conversation is the normal case here, not
an edge case.
"""


def system_message(prompt: str = "You are a helpful agent.") -> str:
    return build_llm_messages(prompt, []).messages[0]["content"]


def test_the_system_message_says_the_tool_list_is_live():
    content = system_message()

    assert TOOL_AUTHORITY in content


def test_it_names_the_specific_trap():
    # Not a vague "tools may vary". The failure was the model trusting
    # ITS OWN earlier words, so that is what the rule has to name.
    lowered = TOOL_AUTHORITY.lower()

    assert "yourself" in lowered or "you said" in lowered
    assert "out of date" in lowered


def test_it_comes_before_the_agents_own_prompt():
    # Same reasoning as harden(): a user's prompt grows, and anything
    # appended to it drifts to the bottom as it does.
    content = system_message("NEVER use tools.")

    assert content.index(TOOL_AUTHORITY) < content.index("NEVER use tools.")


def test_the_injection_defence_is_still_there():
    # This rule sits BESIDE the security preamble, never replaces it.
    content = system_message()

    assert SECURITY_PREAMBLE in content


def test_the_agents_own_prompt_survives_intact():
    content = system_message("Reply only in French.")

    assert "Reply only in French." in content


def test_it_covers_the_toolset_shrinking_too():
    """
    The rule used to cover only growth.

    Routing narrows the toolset every turn - it offers what matches the
    current message - so a change of subject removes the last subject's
    tools. Faced with a GitHub transcript and no GitHub tool, the model
    reconciled them by disowning its own real work: "I made up those
    tools." It had listed the user's actual repositories two messages
    earlier.

    "I cannot do that right now" is recoverable. "I lied to you
    earlier" is not - it teaches the user to distrust the results that
    were true.
    """

    lowered = TOOL_AUTHORITY.lower()

    assert "shrink" in lowered
    assert "made the result up" in lowered or "made up" in lowered
    assert "unavailable" in lowered


def test_it_stays_short():
    # It is paid for on every single turn, forever.
    #
    # Raised from 500 when the shrinking case was added. That case cost
    # a session in which the agent told the user it had fabricated a
    # tool call it had really made, so the extra ~110 characters buy
    # something specific - the bound exists to stop drift, not to stop
    # the rule being complete.
    assert len(TOOL_AUTHORITY) < 700
