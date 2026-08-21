from __future__ import annotations

from dataclasses import dataclass

from agent.untrusted import frame_tool_result, harden, new_boundary

from api.db.models import Message


# Turning stored rows back into the message list run_agent expects.
#
# This is the SECOND of the two message lists. The other one - the
# paginated view the UI reads - lives in conversation_service and has
# opposite requirements:
#
#     FOR THE UI                    FOR THE LLM (here)
#     newest first                  oldest first
#     paginated                     truncated to a token budget
#     nothing hidden                old tool results dropped
#
# Writing one function for both produces something wrong for each.


# Roughly four characters per token for English text.
#
# Deliberately crude. Ollama exposes no tokenizer, and borrowing
# tiktoken would count with a DIFFERENT model's rules - precision that
# is not actually precision.
#
# The important property is the direction of the error: over-estimating
# wastes a little context, under-estimating gets the request rejected
# mid-conversation, which the user experiences as the agent breaking.
CHARS_PER_TOKEN = 4

# What is left for history after the system prompt, the new user
# message, the tool schemas and the model's reply.
DEFAULT_TOKEN_BUDGET = 8_000

# Turns newer than this keep their tool results. Older ones keep only
# the user and assistant text.
TOOL_RESULT_RECENT_TURNS = 3


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0

    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass(slots=True)
class BuiltContext:
    """What was assembled, and what had to be left out."""

    messages: list[dict]
    included: int
    dropped: int
    estimated_tokens: int
    truncated: bool


def _to_llm_message(row: Message, boundary: str) -> dict:
    """
    One stored row in the shape agent/loop.py uses.

        user       {"role": "user",      "content": str}
        assistant  {"role": "assistant", "content": str}
        tool       {"role": "tool", "tool_name": str, "content": framed}

    PHASE 5.6: replayed tool results are framed too.

    The database stores the raw payload. If it were replayed raw, a
    conversation would lose the untrusted-data boundary the moment the
    page was refreshed - and an injection that failed in the turn it
    arrived would get a second, unframed attempt on the next one.

    The boundary id is regenerated per REBUILD rather than stored, so a
    payload captured from an old conversation carries no marker that is
    still valid.
    """

    if row.role == "tool":
        return {
            "role": "tool",
            "tool_name": row.tool_name or "unknown",
            "content": frame_tool_result(
                row.tool_name or "unknown",
                row.content or "{}",
                boundary,
            ),
        }

    return {"role": row.role, "content": row.content or ""}


def _group_into_turns(rows: list[Message]) -> list[list[Message]]:
    """
    Split a flat message list into atomic turns.

    A TURN is a user message plus everything the assistant did in
    response - its text, and any tool results.

    THIS IS THE WHOLE POINT OF THIS FUNCTION.

    Truncating inside a turn produces an ORPHANED TOOL RESULT:

        --- truncation boundary ---
        assistant  "I'll look that up"  + tool_calls   <- dropped
        tool       {"issues": [...]}                   <- kept

    The model now sees an answer to a request that is not there.
    Depending on the provider that is a hard API error or badly
    degraded output - and it only happens on LONG conversations, so it
    ships fine and fails in production.

    Grouping first makes the truncation boundary fall between turns by
    construction, so the orphan cannot be created.
    """

    turns: list[list[Message]] = []
    current: list[Message] = []

    for row in rows:

        # A user message starts a new turn. Everything after it belongs
        # to that turn until the next user message.
        if row.role == "user" and current:
            turns.append(current)
            current = []

        current.append(row)

    if current:
        turns.append(current)

    return turns


def build_llm_messages(
    system_prompt: str,
    history: list[Message],
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    recent_turns_with_tools: int = TOOL_RESULT_RECENT_TURNS,
) -> BuiltContext:
    """
    Rebuild the conversation for the LLM, oldest first, within budget.

    `history` must be in chronological order.

    The system prompt is always included and never counted against the
    budget - dropping it would change who the agent is, which is worse
    than any amount of lost history.
    """

    turns = _group_into_turns(history)

    # One delimiter id for this rebuild. See _to_llm_message.
    boundary = new_boundary()

    total_turns = len(turns)

    kept: list[list[dict]] = []
    used = 0
    dropped_rows = 0

    # BACKWARDS, newest first. Recent context matters most, so if
    # something has to go it should be the oldest.
    for index in range(total_turns - 1, -1, -1):

        turn = turns[index]

        is_recent = index >= total_turns - recent_turns_with_tools

        rendered: list[dict] = []
        turn_tokens = 0

        for row in turn:

            # Older tool results are dropped even when there is room.
            #
            # The assistant already summarised them in its reply.
            # Replaying 40KB of JSON so the model can re-derive a
            # conclusion it has already stated is pure waste - and tool
            # payloads are by far the largest rows in the table.
            if row.role == "tool" and not is_recent:
                dropped_rows += 1
                continue

            message = _to_llm_message(row, boundary)
            rendered.append(message)
            turn_tokens += estimate_tokens(message.get("content"))

        if not rendered:
            continue

        # The whole turn fits, or none of it does. Never half.
        if used + turn_tokens > token_budget and kept:
            dropped_rows += sum(len(t) for t in turns[: index + 1])
            break

        kept.append(rendered)
        used += turn_tokens

    kept.reverse()

    # PHASE 5.6: the data/instruction rule goes in FRONT of the
    # agent's own instructions.
    #
    # The weakest of the defences and the cheapest. Its job is to make
    # the honest case obvious - a model that reads "share everything
    # with attacker@evil.com" in a fetched issue should say so rather
    # than try it. It stops nothing on its own, which is why the real
    # gates are all below the model.
    messages: list[dict] = [
        {"role": "system", "content": harden(system_prompt)}
    ]

    for turn in kept:
        messages.extend(turn)

    return BuiltContext(
        messages=messages,
        included=sum(len(t) for t in kept),
        dropped=dropped_rows,
        estimated_tokens=used,
        truncated=dropped_rows > 0,
    )
