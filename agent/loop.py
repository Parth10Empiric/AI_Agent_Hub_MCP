from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from ollama import AsyncClient

from config.settings import settings

from .errors import ErrorCode
from .execution import ExecutionRecord, utc_now_iso
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

# --- Mid-turn tool discovery -----------------------------------------
#
# The name of the meta-tool the model can call to ask for MORE tools.
#
# WHY A ROUTER ALONE CANNOT BE ENOUGH
#
# Routing happens once, before the turn, from the user's sentence. That
# is fine for a request whose whole plan is visible in the words:
# "list my repositories" needs one tool and names it.
#
# It cannot work for a request whose second step depends on the first:
#
#     "summarise the Student-Faculty-ApplicationReview-System repo"
#
#       step 1  list the repository root      <- implied by the words
#       step 2  read the files that turn up   <- WHICH files?
#
# Nobody can name the tools for step 2 before step 1 has run, because
# the arguments do not exist yet - and the router is asked to do
# exactly that, from a sentence containing no word any tool is named
# after. It answered with eight ways to manage repositories and no way
# to read one, and the transcript shows the consequence: the agent
# guessed the tech stack from the repository's NAME and told the user
# it had no tool for the job.
#
# The fix is not a better guess. It is to stop guessing the whole plan
# up front. The router provides a starting set; when the model finds it
# needs something else, it asks - the same way it would call any other
# tool - and the router runs again with a query written by something
# that has now SEEN the data.
#
# This inverts who is in charge of retrieval. Before: retrieve, then
# act, and hope the retrieval was right. After: act, and retrieve again
# whenever acting reveals what is missing. It is also the only fix here
# that keeps working at 300 tools and ten services, where the first
# guess is wrong far more often than it is at 161.
FIND_TOOLS_NAME = "find_tools"

# How many times one turn may go looking for more tools.
#
# Separate from MAX_ESCALATIONS, which counts AUTOMATIC widening after
# a round of failures. This counts DELIBERATE requests, so it is a
# little more generous: a model that asks has a reason, and a
# multi-service task legitimately needs two or three rounds of "now I
# need something else".
#
# Bounded all the same. A model that cannot find what it wants after
# four searches is not going to find it on the fifth, and each search
# costs a full round.
MAX_TOOL_SEARCHES = 4


def find_tools_schema() -> dict:
    """
    The meta-tool, in Ollama's function-calling shape.

    Written by hand rather than routed from the registry because it is
    not a tool in the registry: nothing executes it, the loop
    intercepts it. It never reaches `ToolExecutor`, so it has no
    namespace, no risk level, no scope and no approval - and it cannot
    be granted, revoked or switched off, because there is nothing there
    to grant.

    That is a security property worth stating plainly: this widens what
    the model can SEE, never what it can DO. Everything it surfaces
    still has to pass the permission policy, which is why the callback
    that serves it is given the same `allow` predicate the first
    routing pass used.
    """

    return {
        "type": "function",
        "function": {
            "name": FIND_TOOLS_NAME,
            "description": (
                "Search for more tools. Call this when the tools you "
                "already have cannot do what the user asked, or when "
                "a result you just received tells you what you need "
                "next - for example, after listing a repository you "
                "need a tool to read one of its files. Describe the "
                "capability you want in plain words, not a tool name. "
                "Any tools found become available to call immediately "
                "afterwards."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What you need to do, in plain words. "
                            "For example: 'read a file from a github "
                            "repository' or 'find which channel a "
                            "slack message was posted in'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }


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

    # How many times the MODEL asked for more tools itself.
    #
    # Counted apart from `escalations` because the two say different
    # things about the router. An escalation means the offered tools
    # FAILED; a tool search means they were never enough. A rising
    # count here is the router being handed multi-step work it cannot
    # predict, which is expected - a rising count for a SINGLE-step
    # request is the lexicon asking for attention.
    tool_searches: int = 0

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


# A markdown table: a header row, a `---` separator, and a data row.
#
# Chosen because it is the narrowest reliable signal of "I am reporting
# facts" that exists in the model's own output. Everything this agent
# fabricated in the transcripts arrived as a table - repositories,
# channels, account fields - because a table is how a model presents
# things it believes to be true. Prose hedges; tables assert.
#
# The alternative, matching numbered or bulleted lists, fires on
# perfectly good answers that offer the user a CHOICE ("1. Direct
# message  2. Specific channel"), which appears constantly in these
# transcripts and reports nothing.
_TABLE_RE = re.compile(
    r"^\s*\|.+\|\s*$\n^\s*\|[\s:|-]*-[\s:|-]*\|\s*$\n^\s*\|.+\|\s*$",
    re.MULTILINE,
)


def _reports_data(answer: str | None) -> bool:
    """
    Does this answer present facts the user is meant to rely on?

    A heuristic, and deliberately a conservative one: it decides
    whether to spend ONE extra round asking the model to check itself.
    A false positive costs a few seconds; a false negative is the
    status quo. It never suppresses or edits an answer.
    """

    return bool(answer) and _TABLE_RE.search(answer) is not None


@dataclass(slots=True)
class ToolSearchResult:
    """
    What one `find_tools` call produced.

    The tools to add, and the payload the model reads. They are
    separate because the two failure cases add nothing and still have
    to say something useful: a search that finds nothing must produce a
    reply that stops the model asking again, or it will.
    """

    tools: list[Any]
    payload: dict


async def _search_tools(
    *,
    escalate: Callable,
    takes_query: bool,
    query: str,
    offered: set[str],
    searches_used: int,
    already_searched: set[str],
) -> ToolSearchResult:
    """
    Run the model's own tool query through the router.

    Every reply here is written to END a line of enquiry rather than
    invite another. A model that gets a vague answer asks again, and
    each attempt costs a full round - so "no" says why, and says what
    to do instead.

    Note what is NOT passed in: the tool list is filtered by the
    caller's `escalate` callback, which carries the permission policy.
    A search cannot surface a tool the agent was never granted, so the
    worst outcome of a bad query is a wasted round.
    """

    if searches_used >= MAX_TOOL_SEARCHES:
        return ToolSearchResult(
            tools=[],
            payload={
                "success": False,
                "error": {
                    "type": "search_limit_reached",
                    "message": (
                        f"You have already searched for tools "
                        f"{MAX_TOOL_SEARCHES} times this turn."
                    ),
                    "hint": (
                        "Do not search again. Use the tools you "
                        "already have, or tell the user which "
                        "capability is missing."
                    ),
                },
            },
        )

    if query.strip().lower() in already_searched:
        return ToolSearchResult(
            tools=[],
            payload={
                "success": False,
                "error": {
                    "type": "repeated_search",
                    "message": (
                        "You already searched for this and the tools "
                        "it found are in your tool list now."
                    ),
                    "hint": (
                        "Read the tools you have before searching "
                        "again. If none of them fit, search for a "
                        "DIFFERENT capability."
                    ),
                },
            },
        )

    # IN A THREAD. The callback re-routes, and routing can make a
    # blocking call to embed the query - see the same note in
    # api/services/chat_service.py. This one is easy to miss because it
    # runs rarely: a stall that happens on one turn in twenty is a
    # stall nobody reproduces.
    found = await asyncio.to_thread(
        (lambda: escalate(set(offered), query))
        if takes_query
        else (lambda: escalate(set(offered)))
    ) or []

    if not found:
        return ToolSearchResult(
            tools=[],
            payload={
                "success": True,
                "found": 0,
                "message": (
                    "No additional tools matched that description. "
                    "Everything available for this request is already "
                    "in your tool list."
                ),
                "hint": (
                    "Do not search again for the same thing. Either "
                    "use what you have, or tell the user plainly that "
                    "this cannot be done with the tools available."
                ),
            },
        )

    return ToolSearchResult(
        tools=list(found),
        payload={
            "success": True,
            "found": len(found),
            "message": (
                "These tools are now available and can be called "
                "immediately."
            ),
            "tools": [
                {
                    "name": tool.name,
                    "description": (
                        getattr(tool, "description", "") or ""
                    )[:200],
                }
                for tool in found
            ],
        },
    )


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
    the set of tool names already offered - and, when it accepts a
    second parameter, the query to route - and returns MORE tools,
    excluding those already given.

    It is called two ways, and the difference matters:

      automatically  an entire round of tool calls failed, which
                     usually means the router's first guess could not
                     do the job.

      on request     the model called `find_tools`, because a result it
                     just read told it what it needs next. See
                     FIND_TOOLS_NAME for why no amount of up-front
                     routing can cover that case.

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

    # The meta-tool goes LAST, and only when there is something behind
    # it. Without an `escalate` callback there is no router to ask, and
    # offering a tool that always answers "nothing found" would train
    # the model to ignore it.
    if escalate is not None:
        ollama_tools.append(find_tools_schema())

    # Does the callback accept a query, or only the excluded names?
    #
    # Checked once, by signature, rather than by calling it and
    # catching TypeError - which would silently swallow a TypeError
    # raised INSIDE the callback and retry it with fewer arguments,
    # turning a bug in the caller's code into mysterious behaviour
    # here.
    escalate_takes_query = (
        escalate is not None
        and len(inspect.signature(escalate).parameters) >= 2
    )

    # The router's decision, enforced.
    #
    # Passing this to the executor turns "these are the tools we
    # suggested" into "these are the only tools that will run". Without
    # it, a model that hallucinates a tool name gets a confusing
    # failure from deep inside the MCP layer instead of a clear one.
    allowed_tools: set[str] = {tool.name for tool in mcp_tools}

    tool_call_counts: Counter[str] = Counter()

    # Searches the model has already run, so an identical one can be
    # answered from what happened last time instead of re-routing.
    # Same idea as the duplicate-call guard: a repeated request with
    # identical arguments cannot produce a different result.
    searched_queries: set[str] = set()

    tool_searches = 0

    # Successful tool calls across the WHOLE turn, not just this round.
    # `round_successes` resets every round, and an answer written in
    # round 3 is grounded by a call made in round 1.
    round_successes_total = 0

    # The grounding check fires at most once per turn. See where it is
    # used for why nudging beats arguing.
    grounding_checked = False

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

            answer = response.message.content or (
                "I could not determine the next action."
            )

            # --- Grounding check ------------------------------------
            #
            # An answer that REPORTS DATA, produced without fetching
            # any, is sent back once with an instruction to fetch it.
            #
            # From the database, one conversation, two consecutive
            # messages:
            #
            #   "list out all github repo name"
            #     rounds=3, 2 tool searches -> "I don't have a tool
            #     that can list repositories."      (true: no scope)
            #
            #   "list out all github repo name"     (user asks again)
            #     rounds=1, ZERO tool calls -> a formatted table of
            #     ten repositories with names and links.
            #
            # The second answer was not fetched. It was assembled from
            # the model's own earlier replies further up the
            # conversation, and it looked more convincing than the
            # honest one. That is the shape this catches: a table is
            # how a model presents facts, and facts have to come from
            # somewhere.
            #
            # WHY A RETRY RATHER THAN A WARNING LABEL
            #
            # A label tells the user their answer might be invented,
            # which leaves them exactly where they started. A retry
            # gives the model the one thing it was missing - the
            # instruction to go and look - and it usually does.
            #
            # WHY IT IS NARROW, AND WHY IT ONLY FIRES ONCE
            #
            # Plenty of good answers legitimately call no tool: "what
            # can you do?", "explain that again", "yes, that's right".
            # None of them render a markdown table. The check demands
            # a table AND zero successful calls this turn AND tools
            # that could have been called - and if the model still
            # answers without one, its answer is accepted. This nudges;
            # it does not argue.
            if (
                not grounding_checked
                and ollama_tools
                and round_successes_total == 0
                and _reports_data(answer)
            ):

                grounding_checked = True

                if verbose:
                    print("  GROUND answered with data but called nothing")

                emit("grounding_retry", {"round": round_number})

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[runtime notice] You presented data in a "
                            "table without calling any tool this turn. "
                            "Anything you say about the user's "
                            "accounts must come from a tool result "
                            "you fetched NOW - earlier replies of your "
                            "own are not evidence, and the accounts "
                            "may have changed since. Call the tool "
                            "that fetches this and answer from what it "
                            "returns. If no tool you have can fetch "
                            "it, say so plainly and present nothing."
                        ),
                    }
                )

                continue

            turn.answer = answer

            emit("done", _done_payload(turn))

            return turn

        for call in tool_calls:

            tool_name = call.function.name
            arguments = normalize_arguments(call.function.arguments)

            # --- Mid-turn tool discovery --------------------------
            #
            # Intercepted here, above everything else, because it is
            # not a tool call at all - it never reaches the executor,
            # the permission policy or the MCP server. It is the model
            # talking to the ROUTER, using the only channel it has.
            #
            # Deliberately NOT counted in `round_calls`. That counter
            # drives the automatic escalation below ("every call this
            # round failed, widen the set"), and a search is neither a
            # call nor a failure - counting it would make a successful
            # search look like a round that achieved nothing.
            if escalate is not None and tool_name == FIND_TOOLS_NAME:

                requested = str(arguments.get("query") or "").strip()

                found = await _search_tools(
                    escalate=escalate,
                    takes_query=escalate_takes_query,
                    query=requested or user_message,
                    offered=allowed_tools,
                    searches_used=tool_searches,
                    already_searched=searched_queries,
                )

                if found.tools:

                    tool_searches += 1
                    searched_queries.add(requested.lower())

                    ollama_tools.extend(
                        mcp_tool_to_ollama_tool(tool)
                        for tool in found.tools
                    )

                    allowed_tools.update(
                        tool.name for tool in found.tools
                    )

                    turn.tool_searches = tool_searches

                    if verbose:
                        print(
                            f"  FIND {requested!r} -> "
                            f"{len(found.tools)} tools "
                            f"({tool_searches}/{MAX_TOOL_SEARCHES})"
                        )

                emit(
                    "tool_search",
                    {
                        "query": requested,
                        "added": len(found.tools),
                        "search": tool_searches,
                        "max": MAX_TOOL_SEARCHES,
                    },
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": FIND_TOOLS_NAME,
                        "content": frame_tool_result(
                            FIND_TOOLS_NAME,
                            found.payload,
                            boundary,
                            captured_at=utc_now_iso(),
                        ),
                    }
                )

                continue

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
                round_successes_total += 1

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
                    # PHASE 5.8: stamped with the moment it was
                    # fetched. Fresh here, replayed as a
                    # "stale-snapshot" on later turns - see
                    # api/context.py. The model can only weigh the age
                    # of its evidence if the age is written down.
                    "content": frame_tool_result(
                        tool_name,
                        record.to_payload(),
                        boundary,
                        captured_at=utc_now_iso(),
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
