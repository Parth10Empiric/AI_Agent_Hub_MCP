from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from ollama import AsyncClient

from config.settings import settings

from .errors import ErrorCode
from .execution import ExecutionRecord
from .executor import ToolExecutor
from .untrusted import frame_tool_result, new_boundary

"""
The agent loop (Phase 2.9).

The loop's job is narrow: talk to the LLM, and when it asks for a tool,
get that tool run. It does NOT decide whether a call is allowed, how to
handle a timeout, or whether to retry - all of that moved into
`ToolExecutor`.

That split is worth stating plainly, because the previous version of
this file did everything:

    before                          after
    ------------------------------  ---------------------------------
    call the tool inline            hand the call to the executor
    one bare `except Exception`     16 classified error codes
    no retries                      retries, but only where safe
    no permission check             policy + approval before every call
    print() for observability       an ExecutionRecord per call

The loop got shorter and the system got stronger. That is usually what
a good boundary looks like.
"""


MAX_TOOL_ROUNDS = 50

# How many times one turn may ask the router for a DIFFERENT set of
# tools after the first set failed.
#
# The router picks tools before the model has tried anything. Sometimes
# that guess is wrong in a way only execution reveals: the chosen tool
# needs arguments nobody has, or the service exposes the capability
# under a name the router ranked too low.
#
# When every tool call in a round fails, we re-route with the tools
# already offered EXCLUDED, and hand the model a fresh set. This is
# second-chance retrieval - retrying the RETRIEVAL, not the call.
#
# Capped at 2 because a third pass is reaching into tools the router
# scored badly for good reason, and each pass costs a full LLM round.
MAX_ESCALATIONS = 2

# The only failures a DIFFERENT tool could plausibly fix.
#
# The first version of this escalated on any failed round, and a real
# session showed why that is wrong:
#
#     FAIL google_drive_list_folder    [invalid_arguments]
#     WIDEN added 6 more tools
#     FAIL google_drive_search_files   [unknown]   21925ms
#     WIDEN added 20 more tools
#     FAIL google_drive_get_file       [unknown]    1474ms
#
# Those "unknown" failures were Google Drive's OAuth flow timing out.
# The tool set was never the problem, so widening it twice achieved
# nothing except two extra LLM rounds and 23 wasted seconds.
#
# The distinction that matters:
#
#   Could a different tool have succeeded here?
#     yes -> the model asked for something it was not given, or could
#            not supply an argument. Another tool may avoid that.
#     no  -> the service is down, the token is expired, the policy
#            said no. Every tool in that service will fail the same
#            way, so trying more of them just wastes the user's time.
ESCALATABLE_ERRORS = frozenset({
    ErrorCode.TOOL_NOT_FOUND,
    ErrorCode.TOOL_NOT_AVAILABLE,
    ErrorCode.INVALID_ARGUMENTS,
    ErrorCode.NOT_FOUND,
})

# How many times the same tool may be called with identical arguments
# before we stop it.
#
# LLMs get stuck. A model that calls github_list_issues, dislikes the
# answer, and calls it again with the same arguments will do that
# forever. This is a cheap circuit breaker: identical call, identical
# result, no progress - so refuse and tell the model to try something
# else.
MAX_DUPLICATE_CALLS = 2


# The Ollama client, created once and shared.
#
# This MUST be the async client. The synchronous `ollama.chat()` blocks
# the thread it runs on, and in an async server that thread IS the
# event loop - so one user's 20-second LLM call freezes every other
# user's request, every MCP read and every SSE heartbeat.
#
# The CLI never showed this: one user, nothing else to block. Phase 3
# would have shown it at the second concurrent user.
#
# Same rule as ConsoleApproval wrapping input() in asyncio.to_thread:
# never call a blocking function directly inside async code.
_client = AsyncClient()


@dataclass(slots=True)
class AgentTurn:
    """
    The outcome of one user message.

    Returns the executions alongside the answer, rather than just the
    text, because the tool timeline is not debug output - it is a
    product feature (AI_Agent_Hub.md section 13) and the data the
    Phase 3 API will stream to the UI.
    """

    answer: str
    executions: list[ExecutionRecord] = field(default_factory=list)
    rounds: int = 0

    # How many times the tool set had to be widened mid-turn.
    # A non-zero value here means the router's first pick was wrong -
    # useful signal for tuning the lexicon.
    escalations: int = 0

    # --- Token accounting -------------------------------------------
    #
    # Ollama reports token counts PER RESPONSE, and one user task is
    # usually several responses (one per tool round). So these are
    # summed across the turn rather than read off the final response,
    # which would report only the last round and wildly undercount a
    # 5-round turn.
    #
    # prompt_tokens : tokens the model READ   (system + history + tool
    #                 results). Grows every round, because each round
    #                 re-sends the whole conversation - this is why a
    #                 chatty tool result is expensive twice over.
    # eval_tokens   : tokens the model WROTE  (thinking, tool calls,
    #                 final answer).
    prompt_tokens: int = 0
    eval_tokens: int = 0

    # Per-round breakdown: [(round, prompt, eval), ...].
    # Useful for spotting WHICH round blew up the context.
    token_rounds: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.eval_tokens

    @property
    def failed_executions(self) -> list[ExecutionRecord]:
        return [
            record
            for record in self.executions
            if not record.succeeded
        ]

    @property
    def total_tool_ms(self) -> float:
        return sum(
            record.duration_ms
            for record in self.executions
        )


def mcp_tool_to_ollama_tool(tool: Any) -> dict:
    """
    Convert an MCP Tool definition into Ollama's function-tool schema.
    """

    if hasattr(tool, "model_dump"):
        data = tool.model_dump()
    else:
        data = {
            "name": getattr(tool, "name", None),
            "description": getattr(tool, "description", None),
            "inputSchema": getattr(tool, "inputSchema", None)
            or getattr(tool, "input_schema", None),
        }

    # BOTH spellings, deliberately.
    #
    # The MCP wire format uses "inputSchema" (camelCase), but the
    # Python SDK's field is `input_schema` and model_dump() returns the
    # FIELD name, not the alias. Reading only "inputSchema" silently
    # fell through to the empty-object default, so every tool reached
    # the model with NO parameters - the model then called it with no
    # arguments and the server rejected the call as invalid.
    #
    # A wrong tool schema does not raise; it just makes the agent
    # useless. Accept either key rather than depend on SDK casing.
    parameters = (
        data.get("inputSchema")
        or data.get("input_schema")
        or {"type": "object", "properties": {}}
    )

    return {
        "type": "function",
        "function": {
            "name": data["name"],
            "description": data.get("description") or "",
            "parameters": parameters,
        },
    }


def normalize_arguments(arguments: Any) -> dict:
    """
    Normalize Ollama tool arguments.

    Depending on the Ollama version and the model, arguments arrive
    either as a dict or as a JSON string. Normalizing here means the
    executor only ever sees one shape.
    """

    if arguments is None:
        return {}

    if isinstance(arguments, dict):
        return arguments

    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def make_tool_call_key(tool_name: str, arguments: dict) -> str:
    """
    A stable identifier for one tool call, used to detect repeats.

    `sort_keys=True` is what makes it stable: without it,
    {"owner": "a", "repo": "b"} and {"repo": "b", "owner": "a"} would
    produce different keys and the loop guard would never fire.
    """

    return f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"


def _done_payload(turn: AgentTurn) -> dict:
    """
    The closing summary for one turn.

    Kept out of run_agent so both exit paths - a normal answer and
    hitting the round limit - report exactly the same shape. A UI that
    handled only one of them would hang on the other.
    """

    return {
        "answer": turn.answer,
        "rounds": turn.rounds,
        "escalations": turn.escalations,
        "executions": len(turn.executions),
        "failed": len(turn.failed_executions),
        "total_tool_ms": round(turn.total_tool_ms, 2),
        "prompt_tokens": turn.prompt_tokens,
        "eval_tokens": turn.eval_tokens,
        "total_tokens": turn.total_tokens,
    }


async def run_agent(
    session: Any,
    mcp_tools: list[Any],
    user_message: str,
    messages: list[dict],
    executor: ToolExecutor,
    *,
    model: str | None = None,
    verbose: bool = True,
    escalate: Callable[[set[str]], list[Any]] | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> AgentTurn:
    """
    Run one conversational turn to completion.

    Loops: ask the model, run any tools it requests, feed the results
    back, repeat until it answers without asking for a tool.

    `escalate` is an optional second-chance retrieval hook. It receives
    the set of tool names already offered and returns MORE tools,
    excluding those. The loop calls it when an entire round of tool
    calls failed, which usually means the router's first guess could
    not do the job.

    It is a callback rather than a router reference on purpose: the
    loop stays testable with a plain lambda, and it never needs to know
    that a router, an index or a lexicon exist.
    """

    model = model or settings.ollama_model

    messages.append(
        {
            "role": "user",
            "content": user_message,
        }
    )

    ollama_tools = [
        mcp_tool_to_ollama_tool(tool)
        for tool in mcp_tools
    ]

    # The router's decision, enforced.
    #
    # Passing this to the executor turns "these are the tools we
    # suggested" into "these are the only tools that will run". Without
    # it, a model that hallucinates a tool name gets a confusing
    # failure from deep inside the MCP layer instead of a clear one.
    allowed_tools: set[str] = {tool.name for tool in mcp_tools}

    tool_call_counts: Counter[str] = Counter()

    # PHASE 5.6: the delimiter id for THIS turn.
    #
    # Every tool result is wrapped in a block tagged with it, so
    # content fetched from a GitHub issue or a Drive file cannot forge
    # a closing marker and have the rest of itself read as
    # conversation. It cannot know a value generated after it was
    # written.
    boundary = new_boundary()

    escalations = 0

    turn = AgentTurn(answer="")

    def emit(event: str, data: dict) -> None:
        """
        Report progress to whoever is watching this turn.

        The CLI passes a callback that prints; the Phase 3 API passes
        one that pushes an SSE frame. The loop itself stays ignorant of
        both - it only knows that something happened worth reporting.

        Deliberately swallows callback errors: a broken UI must never
        abort a turn that is mid-way through real tool calls.
        """

        if on_event is None:
            return

        try:
            on_event(event, data)
        except Exception:
            pass

    for round_number in range(1, MAX_TOOL_ROUNDS + 1):

        turn.rounds = round_number

        emit("round_start", {"round": round_number})

        # Tracked per round so we can tell "this round achieved
        # nothing" from "this round did some work".
        round_calls = 0
        round_successes = 0

        # Failures that a DIFFERENT tool could plausibly have avoided.
        # A round full of service outages is not one of them.
        round_escalatable = 0

        response = await _client.chat(
            model=model,
            messages=messages,
            tools=ollama_tools,
            think=True,
        )

        # `or 0`, not just a getattr default.
        #
        # These fields exist on the response object but are None when
        # Ollama serves a request from its prompt cache - and `int +=
        # None` raises TypeError, which would kill a turn over a
        # statistic nobody asked to be load-bearing.
        round_prompt_tokens = getattr(response, "prompt_eval_count", 0) or 0
        round_eval_tokens = getattr(response, "eval_count", 0) or 0

        turn.prompt_tokens += round_prompt_tokens
        turn.eval_tokens += round_eval_tokens

        turn.token_rounds.append(
            (round_number, round_prompt_tokens, round_eval_tokens)
        )

        if verbose:
            print(
                f"  TOKENS round {round_number}: "
                f"in {round_prompt_tokens:,}  "
                f"out {round_eval_tokens:,}  "
                f"(turn so far {turn.total_tokens:,})"
            )

        emit(
            "tokens",
            {
                "round": round_number,
                "prompt_tokens": round_prompt_tokens,
                "eval_tokens": round_eval_tokens,
                "turn_total": turn.total_tokens,
            },
        )

        messages.append(response.message)

        tool_calls = response.message.tool_calls or []

        # No tool call means the model is done reasoning and has an
        # answer for the user.
        if not tool_calls:
            turn.answer = response.message.content or (
                "I could not determine the next action."
            )

            emit("done", _done_payload(turn))

            return turn

        for call in tool_calls:

            tool_name = call.function.name
            arguments = normalize_arguments(call.function.arguments)

            # --- Loop guard ---------------------------------------
            #
            # Checked BEFORE the executor, not inside it. Repetition is
            # a property of the conversation, not of a single call -
            # the executor deliberately has no memory of previous
            # calls, which is what makes it safe to share between
            # concurrent conversations.

            round_calls += 1

            call_key = make_tool_call_key(tool_name, arguments)
            tool_call_counts[call_key] += 1

            if tool_call_counts[call_key] > MAX_DUPLICATE_CALLS:

                if verbose:
                    print(
                        f"  LOOP {tool_name} repeated with identical "
                        "arguments - refusing"
                    )

                emit(
                    "duplicate_call",
                    {
                        "tool": tool_name,
                        "calls": tool_call_counts[call_key],
                    },
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        # Framed like any other tool message. This one
                        # is ours, not a service's - but a message that
                        # looks different is a message worth imitating.
                        "content": frame_tool_result(
                            tool_name,
                            {
                                "success": False,
                                "error": {
                                    "type": "repeated_tool_call",
                                    "message": (
                                        "This exact call was already "
                                        "made and returned the same "
                                        "result."
                                    ),
                                    "hint": (
                                        "Do not repeat it. Choose a "
                                        "different action or answer "
                                        "the user with what you have."
                                    ),
                                },
                            },
                            boundary,
                        ),
                    }
                )

                continue

            # --- Execute ------------------------------------------

            emit(
                "tool_start",
                {"tool": tool_name, "round": round_number},
            )

            record = await executor.execute(
                session=session,
                tool_name=tool_name,
                arguments=arguments,
                allowed_tools=allowed_tools,
            )

            turn.executions.append(record)

            # to_dict() is already redacted (execution.py), so this is
            # safe to send straight to a browser.
            emit("tool_end", record.to_dict())

            if record.succeeded:
                round_successes += 1

            elif (
                record.error is not None
                and record.error.code in ESCALATABLE_ERRORS
            ):
                round_escalatable += 1

            if verbose:
                print(f"  {record.summary_line()}")

            # The payload shape is identical for success and failure,
            # so the model reads tool results one consistent way.
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,

                    # PHASE 5.6: the untrusted-data boundary.
                    #
                    # THIS is the line that carries a GitHub issue body
                    # into the model's context. Whatever a stranger
                    # wrote in it arrives here verbatim - nothing is
                    # filtered, because filtering text for "ignore
                    # previous instructions" catches the phrase and not
                    # the idea.
                    #
                    # What changes is the STRUCTURE around it: a
                    # delimited block that says where the data came
                    # from and that it is data. Defence in depth, not a
                    # control - the controls are the agent's scopes,
                    # the tool switch and the human approval, none of
                    # which the model can talk its way past.
                    "content": frame_tool_result(
                        tool_name,
                        record.to_payload(),
                        boundary,
                    ),
                }
            )

        # --- Second-chance retrieval ------------------------------
        #
        # Every tool the model tried this round failed. That is the
        # clearest possible signal that the tools it was given cannot
        # do the job - so give it different ones rather than letting
        # it keep failing with the same set.
        #
        # Real example this was built for:
        #
        #   "check github drive slack and calander connection"
        #     -> the only calendar tool offered was freebusy, which
        #        needs three arguments the user never supplied.
        #        Every calendar call failed. Escalating brings in
        #        google_calendar_list_calendars, which needs none.

        needs_help = (
            round_calls > 0
            and round_successes == 0
            and round_escalatable > 0
            and escalate is not None
            and escalations < MAX_ESCALATIONS
        )

        if needs_help:

            extra = escalate(set(allowed_tools))

            if extra:

                escalations += 1

                ollama_tools.extend(
                    mcp_tool_to_ollama_tool(tool)
                    for tool in extra
                )

                allowed_tools.update(tool.name for tool in extra)

                if verbose:
                    print(
                        f"  WIDEN every call failed - added "
                        f"{len(extra)} more tools "
                        f"({escalations}/{MAX_ESCALATIONS})"
                    )

                emit(
                    "escalation",
                    {
                        "added": len(extra),
                        "escalation": escalations,
                        "max": MAX_ESCALATIONS,
                    },
                )

                turn.escalations = escalations

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Those tools did not work. You now have "
                            "additional tools available. Use them to "
                            "answer, or explain what you cannot do."
                        ),
                    }
                )

    turn.answer = (
        "I stopped because the maximum number of tool execution "
        "rounds was reached."
    )

    emit("done", _done_payload(turn))

    return turn
