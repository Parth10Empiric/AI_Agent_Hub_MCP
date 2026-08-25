from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import agent.loop as loop_module  # noqa: E402
from agent.execution import (  # noqa: E402
    ExecutionRecord,
    ExecutionStatus,
    utc_now_iso,
)
from agent.loop import (  # noqa: E402
    FIND_TOOLS_NAME,
    MAX_TOOL_SEARCHES,
    run_agent,
)

"""
The model can ask for tools it was not given.

WHY THIS EXISTS AT ALL

Routing runs once, before the turn, from the user's sentence. That is
enough for a request whose whole plan is in the words - "list my
repositories" names its own tool. It cannot work for a request whose
second step depends on the first:

    "summarise the Student-Faculty-ApplicationReview-System repo"

      step 1  list the repository root      implied by the sentence
      step 2  read the files it turns up    WHICH files?

Nobody can name the tools for step 2 before step 1 has run, because the
arguments do not exist yet. The router was asked to do exactly that,
from a sentence containing no word any tool is named after, and
answered with eight ways to manage repositories and no way to read one.

`find_tools` inverts who is in charge of retrieval. Instead of
retrieve-then-act-and-hope, the model acts, sees a result, and asks for
what that result showed it needs. No first-shot router can be tuned
into this, which is why it is the one fix here that keeps working as
the catalogue grows.

WHAT IT DELIBERATELY IS NOT

An escape hatch from permissions. The loop never executes it - it is
intercepted above the executor - and the tools it returns come from the
same callback, carrying the same policy filter, as the first routing
pass. It widens what the model can SEE. Nothing widens what it can DO.
"""


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class FakeTool:
    """An MCP tool, reduced to what mcp_tool_to_ollama_tool reads."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.inputSchema = {"type": "object", "properties": {}}


class FakeFunction:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments


class FakeCall:
    def __init__(self, name: str, arguments: dict) -> None:
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content: str = "", tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class FakeResponse:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message
        self.prompt_eval_count = 10
        self.eval_count = 5


class FakeClient:
    """
    Replays a scripted conversation and records what it was offered.

    `offered` is the interesting half: it captures the tool list on
    every round, which is what proves the set actually changed
    mid-turn rather than being fixed before the first call.
    """

    def __init__(self, script: list[FakeResponse]) -> None:
        self._script = list(script)
        self.offered: list[list[str]] = []

    async def chat(self, *, model, messages, tools, think):

        self.offered.append([
            tool["function"]["name"]
            for tool in tools
        ])

        if not self._script:
            return FakeResponse(FakeMessage(content="done"))

        return self._script.pop(0)


class ExplodingExecutor:
    """
    An executor that fails the test if it is ever reached.

    `find_tools` must never arrive here: it is not in the registry, has
    no namespace, no scope and no risk level, so an executor that saw
    it would have nothing to check it against.
    """

    async def execute(self, **kwargs):
        raise AssertionError(
            f"the executor was called with {kwargs.get('tool_name')!r}"
        )


def search_for(query: str) -> FakeResponse:
    return FakeResponse(
        FakeMessage(tool_calls=[FakeCall(FIND_TOOLS_NAME, {"query": query})])
    )


def answer(text: str = "Here is the summary.") -> FakeResponse:
    return FakeResponse(FakeMessage(content=text))


def drive(script, escalate, monkeypatch, tools=None):
    """Run one turn against a scripted model."""

    client = FakeClient(script)

    monkeypatch.setattr(loop_module, "_client", client)

    turn = asyncio.run(
        run_agent(
            session=None,
            mcp_tools=tools if tools is not None else [
                FakeTool("github_list_repositories")
            ],
            user_message="summarise the argus/test repo",
            messages=[],
            executor=ExplodingExecutor(),
            verbose=False,
            escalate=escalate,
        )
    )

    return turn, client


# ---------------------------------------------------------------------
# It is offered
# ---------------------------------------------------------------------


def test_the_model_is_offered_the_search_tool(monkeypatch):
    _, client = drive([answer()], lambda names, query: [], monkeypatch)

    assert FIND_TOOLS_NAME in client.offered[0]


def test_it_is_not_offered_when_there_is_nothing_behind_it(monkeypatch):
    # No callback means no router to ask. A tool that always answers
    # "nothing found" teaches the model to stop calling it.
    _, client = drive([answer()], None, monkeypatch)

    assert FIND_TOOLS_NAME not in client.offered[0]


# ---------------------------------------------------------------------
# It widens the set MID-TURN
# ---------------------------------------------------------------------


def test_a_search_adds_tools_for_the_next_round(monkeypatch):
    def escalate(offered, query):
        return [FakeTool("github_get_file", "Read a repository file.")]

    turn, client = drive(
        [search_for("read a file from a repository"), answer()],
        escalate,
        monkeypatch,
    )

    # THE POINT OF THE WHOLE FEATURE: the tool list is different on
    # round 2 from what it was on round 1. Before this, `ollama_tools`
    # was built once before the loop and frozen for all 50 rounds - so
    # a model that worked out what it needed in round 2 could never be
    # given it.
    assert "github_get_file" not in client.offered[0]
    assert "github_get_file" in client.offered[1]

    assert turn.tool_searches == 1


def test_the_model_writes_its_own_query(monkeypatch):
    # Not the user's sentence. The user said "summarise the repo"; by
    # the time the model asks, it has seen the file listing and knows
    # it needs to read a specific kind of file. That is information the
    # router could not have had before the turn started.
    seen = []

    def escalate(offered, query):
        seen.append(query)
        return [FakeTool("github_get_file")]

    drive([search_for("read package.json from a repo"), answer()],
          escalate, monkeypatch)

    assert seen == ["read package.json from a repo"]


def test_tools_already_offered_are_excluded_from_the_search(monkeypatch):
    seen = []

    def escalate(offered, query):
        seen.append(offered)
        return []

    drive([search_for("anything"), answer()], escalate, monkeypatch)

    assert "github_list_repositories" in seen[0]


def test_a_one_argument_callback_still_works(monkeypatch):
    # The automatic escalation hook predates this feature and takes
    # only the excluded names. Checked by signature rather than by
    # calling and catching TypeError, which would swallow a real
    # TypeError raised inside the callback.
    def escalate(offered):
        return [FakeTool("github_get_file")]

    turn, client = drive(
        [search_for("read a file"), answer()],
        escalate,
        monkeypatch,
    )

    assert "github_get_file" in client.offered[1]
    assert turn.tool_searches == 1


# ---------------------------------------------------------------------
# It never becomes a tool call
# ---------------------------------------------------------------------


def test_the_search_never_reaches_the_executor(monkeypatch):
    # ExplodingExecutor raises if it is called. Reaching the executor
    # would mean asking the permission policy about a tool that has no
    # namespace, no scope and no risk level.
    turn, _ = drive(
        [search_for("read a file"), answer()],
        lambda names, query: [FakeTool("github_get_file")],
        monkeypatch,
    )

    assert turn.executions == []


def test_a_search_does_not_look_like_a_failed_round(monkeypatch):
    # A round containing only a search made zero tool CALLS, so the
    # automatic "everything failed, widen the set" path must not fire
    # - that would spend an escalation on a round that went fine.
    turn, _ = drive(
        [search_for("read a file"), answer()],
        lambda names, query: [FakeTool("github_get_file")],
        monkeypatch,
    )

    assert turn.escalations == 0


# ---------------------------------------------------------------------
# It is bounded
# ---------------------------------------------------------------------


def test_repeating_the_same_search_is_refused(monkeypatch):
    calls = []

    def escalate(offered, query):
        calls.append(query)
        return [FakeTool(f"github_tool_{len(calls)}")]

    turn, _ = drive(
        [search_for("read a file"), search_for("read a file"), answer()],
        escalate,
        monkeypatch,
    )

    # The second one never reached the router: an identical search
    # cannot return anything the first did not.
    assert calls == ["read a file"]
    assert turn.tool_searches == 1


def test_searching_is_capped(monkeypatch):
    def escalate(offered, query):
        return [FakeTool(f"tool_for_{query}")]

    script = [
        search_for(f"capability number {index}")
        for index in range(MAX_TOOL_SEARCHES + 3)
    ]
    script.append(answer())

    turn, _ = drive(script, escalate, monkeypatch)

    assert turn.tool_searches == MAX_TOOL_SEARCHES


def test_finding_nothing_says_so_plainly(monkeypatch):
    # A vague answer gets asked again, and every attempt costs a full
    # round. The reply has to end the line of enquiry.
    captured = {}

    class Recorder(FakeClient):
        async def chat(self, *, model, messages, tools, think):
            captured["messages"] = list(messages)
            return await super().chat(
                model=model,
                messages=messages,
                tools=tools,
                think=think,
            )

    client = Recorder([search_for("do the impossible"), answer()])

    monkeypatch.setattr(loop_module, "_client", client)

    asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="do something impossible",
            messages=[],
            executor=ExplodingExecutor(),
            verbose=False,
            escalate=lambda names, query: [],
        )
    )

    reply = next(
        message
        for message in captured["messages"]
        # The model's own replies are appended as objects, not dicts.
        if isinstance(message, dict)
        and message.get("tool_name") == FIND_TOOLS_NAME
    )

    assert "No additional tools matched" in reply["content"]
    assert "Do not search again" in reply["content"]


def test_the_search_result_is_framed_like_any_other(monkeypatch):
    # A message that looks different is a message worth imitating. The
    # boundary block is how the model tells data from instructions, and
    # a reply from OUR OWN code is not an exception worth teaching.
    captured = {}

    class Recorder(FakeClient):
        async def chat(self, *, model, messages, tools, think):
            captured["messages"] = list(messages)
            return await super().chat(
                model=model,
                messages=messages,
                tools=tools,
                think=think,
            )

    client = Recorder([search_for("read a file"), answer()])

    monkeypatch.setattr(loop_module, "_client", client)

    asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="summarise the repo",
            messages=[],
            executor=ExplodingExecutor(),
            verbose=False,
            escalate=lambda names, query: [FakeTool("github_get_file")],
        )
    )

    reply = next(
        message
        for message in captured["messages"]
        # The model's own replies are appended as objects, not dicts.
        if isinstance(message, dict)
        and message.get("tool_name") == FIND_TOOLS_NAME
    )

    assert reply["content"].startswith("[tool_result ")
    assert "github_get_file" in reply["content"]


# ---------------------------------------------------------------------
# The ordinary path still works
# ---------------------------------------------------------------------


class RecordingExecutor:
    """An executor that succeeds and remembers what it was asked."""

    def __init__(self) -> None:
        self.called: list[str] = []

    async def execute(self, *, session, tool_name, arguments, allowed_tools):

        self.called.append(tool_name)

        return ExecutionRecord(
            execution_id="exec-1",
            tool_name=tool_name,
            status=ExecutionStatus.SUCCESS,
            started_at=utc_now_iso(),
            duration_ms=1.0,
            result={"ok": True},
        )


def test_a_real_tool_call_after_a_search_runs_normally(monkeypatch):
    # The interception sits above every other call in the loop, so it
    # is exactly where a mistake would silently swallow ordinary tool
    # calls - and every other test in this file uses an executor that
    # refuses to run.
    executor = RecordingExecutor()

    client = FakeClient([
        search_for("read a file"),
        FakeResponse(
            FakeMessage(
                tool_calls=[
                    FakeCall("github_get_file", {"owner": "a", "repo": "b"}),
                ]
            )
        ),
        answer(),
    ])

    monkeypatch.setattr(loop_module, "_client", client)

    turn = asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="summarise the repo",
            messages=[],
            executor=executor,
            verbose=False,
            escalate=lambda names, query: [FakeTool("github_get_file")],
        )
    )

    assert executor.called == ["github_get_file"]
    assert len(turn.executions) == 1
    assert turn.tool_searches == 1
    assert turn.answer == "Here is the summary."


def test_a_fresh_tool_result_is_stamped_with_its_capture_time(monkeypatch):
    # The whole freshness fix downstream - the stale-snapshot label in
    # api/context.py, the age, the credential cutoff - reads this
    # timestamp. If the loop stops writing it, every one of those
    # becomes a no-op and nothing fails loudly.
    captured = {}

    class Recorder(FakeClient):
        async def chat(self, *, model, messages, tools, think):
            captured["messages"] = list(messages)
            return await super().chat(
                model=model,
                messages=messages,
                tools=tools,
                think=think,
            )

    client = Recorder([
        FakeResponse(
            FakeMessage(
                tool_calls=[FakeCall("github_list_repositories", {})]
            )
        ),
        answer(),
    ])

    monkeypatch.setattr(loop_module, "_client", client)

    asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="list my repos",
            messages=[],
            executor=RecordingExecutor(),
            verbose=False,
        )
    )

    result = next(
        message
        for message in captured["messages"]
        if isinstance(message, dict)
        and message.get("tool_name") == "github_list_repositories"
    )

    assert 'captured="' in result["content"]
    assert 'trust="untrusted-data"' in result["content"]


# ---------------------------------------------------------------------
# Grounding: an answer that reports data must have fetched it
# ---------------------------------------------------------------------


TABLE_ANSWER = """You currently have 2 repositories:

| # | Name | Visibility |
|---|------|------------|
| 1 | Email_Spam_Detection | Public |
| 2 | flower_classification | Public |
"""


def test_a_table_with_no_tool_call_is_sent_back_once(monkeypatch):
    # From the database, two consecutive messages, same question:
    #
    #   rounds=3, 2 searches -> "I don't have a tool for that."  (true)
    #   rounds=1, 0 calls    -> a table of ten repositories.     (invented)
    #
    # The second answer was assembled from the model's own earlier
    # replies. It called nothing, and it looked more convincing than
    # the honest answer that preceded it.
    client = FakeClient([
        answer(TABLE_ANSWER),
        answer("I cannot list them without a tool."),
    ])

    monkeypatch.setattr(loop_module, "_client", client)

    turn = asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="list out all github repo name",
            messages=[],
            executor=ExplodingExecutor(),
            verbose=False,
        )
    )

    # It was asked to check itself, and its second answer is the one
    # the user sees.
    assert turn.rounds == 2
    assert turn.answer == "I cannot list them without a tool."


def test_a_table_backed_by_a_real_call_is_accepted(monkeypatch):
    # The check is about EVIDENCE, not about tables. A turn that
    # actually fetched the data must not be second-guessed.
    client = FakeClient([
        FakeResponse(
            FakeMessage(
                tool_calls=[FakeCall("github_list_repositories", {})]
            )
        ),
        answer(TABLE_ANSWER),
    ])

    monkeypatch.setattr(loop_module, "_client", client)

    turn = asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="list my repos",
            messages=[],
            executor=RecordingExecutor(),
            verbose=False,
        )
    )

    assert turn.rounds == 2
    assert turn.answer == TABLE_ANSWER


def test_an_ordinary_answer_is_never_second_guessed(monkeypatch):
    # Most good answers call no tool: "what can you do?", "explain that
    # again", "yes". None of them render a table, and none of them
    # should cost an extra round.
    client = FakeClient([answer("I can read your GitHub repositories.")])

    monkeypatch.setattr(loop_module, "_client", client)

    turn = asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="what can you do?",
            messages=[],
            executor=ExplodingExecutor(),
            verbose=False,
        )
    )

    assert turn.rounds == 1


def test_the_check_nudges_once_and_then_accepts(monkeypatch):
    # It does not argue. If the model insists on its table after being
    # asked to fetch it, the user sees the table - the loop's job is to
    # give the model the instruction it was missing, not to win.
    client = FakeClient([
        answer(TABLE_ANSWER),
        answer(TABLE_ANSWER),
    ])

    monkeypatch.setattr(loop_module, "_client", client)

    turn = asyncio.run(
        run_agent(
            session=None,
            mcp_tools=[FakeTool("github_list_repositories")],
            user_message="list out all github repo name",
            messages=[],
            executor=ExplodingExecutor(),
            verbose=False,
        )
    )

    assert turn.rounds == 2
    assert turn.answer == TABLE_ANSWER
