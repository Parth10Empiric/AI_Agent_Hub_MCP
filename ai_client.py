import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.engine import AgentEngine
from agent.loop import AgentTurn, run_agent
from agent.permissions import ConsoleApproval
from agent.routing import RoutingDecision
from agent.untrusted import DATA_FRESHNESS

from ollama import AsyncClient, ResponseError

from config.settings import settings


SYSTEM_PROMPT = (
    "You are an AI assistant connected to an MCP server.\n\n"

    "Use available tools when they are necessary.\n"
    "Do not invent tool results.\n"
    "Prefer the minimum number of tool calls needed to answer "
    "the user.\n\n"

    "IMPORTANT:\n"
    "Maintain context from previous messages in the current "
    "conversation.\n"
    "When the user refers to something such as 'it', 'them', "
    "'those issues', 'that repository', or 'the previous result', "
    "use the conversation history to resolve the reference.\n\n"

    # PHASE 5.8, and it sits directly after the rule above because the
    # two pull in opposite directions and the model has to hold both.
    #
    # "Use the conversation history to resolve the reference" is about
    # what the user MEANT - "that repository" is the one from two
    # messages ago. It is not permission to reuse what the tool
    # RETURNED about it. Left alone, the model reads the first rule as
    # licence to answer any repeated question from the transcript, and
    # a stale answer is indistinguishable from a fresh one.
    #
    # The web path gets this through api/context.py, which also removes
    # the stale payloads outright. The CLI keeps its whole transcript
    # in memory and has no equivalent, so here the prompt is the only
    # defence - which is exactly why the timestamps in every
    # [tool_result] block matter: this rule is unusable without them.
    + DATA_FRESHNESS
)


def print_startup_summary(engine: AgentEngine) -> None:
    """
    Show what was discovered and how it was classified.

    Printed once at startup, because a classification mistake is far
    cheaper to notice here than to discover when an agent deletes
    something it should have asked about first.
    """

    summary = engine.describe()

    print("\nTool Registry")
    print("-" * 46)
    print(f"Total tools        : {summary['total_tools']}")
    print(f"Read-only          : {summary['read_only']}")
    print(f"Requires approval  : {summary['requires_approval']}")

    print("\nServices:")

    for service, count in summary["by_service"].items():
        print(f"  {service:<22}{count:>3} tools")

    print("\nRisk levels:")

    for level, count in summary["by_risk"].items():
        if count:
            print(f"  {level:<22}{count:>3} tools")


def print_routing(decision: RoutingDecision) -> None:
    """
    Show the router's reasoning for one message.

    This is the console version of the execution timeline that Phase
    2.8 will render in the Agent Hub UI. Keeping it visible while you
    build is the fastest way to spot a missing alias or a bad weight.
    """

    print("\n[Router] Analyzing request...")

    if decision.intent is not None:
        print(f"[Router] Intent      : {decision.intent.value}")

    if decision.namespace_scores:

        services = ", ".join(
            f"{entry.namespace} ({entry.score:.2f})"
            for entry in decision.namespace_scores
        )

        print(f"[Router] Services    : {services}")

    if decision.unmatched_tokens:
        print(
            "[Router] Unmatched   : "
            f"{', '.join(decision.unmatched_tokens)}"
        )

    print(
        f"[Router] Confidence  : {decision.confidence:.2f}  "
        f"({decision.duration_ms:.1f} ms)"
    )

    if decision.fallback_used:
        print(
            "[Router] LOW CONFIDENCE - widened the tool set. "
            "Consider adding an alias for this phrasing."
        )

    print(f"[Router] Selected    : {len(decision.candidates)} tools")

    for candidate in decision.candidates:
        print(f"    {candidate.score:.3f}  {candidate.tool_name}")

    needs_approval = decision.requires_approval()

    if needs_approval:
        print(
            "[Router] Approval-gated if called: "
            f"{', '.join(needs_approval)}"
        )


def print_timeline(turn: AgentTurn) -> None:
    """
    The execution timeline from AI_Agent_Hub.md section 13.

    Every number here comes straight off the ExecutionRecord objects
    the executor produced. This console version is the same data the
    Phase 3 API will stream to the web UI - which is the point of
    recording it as structured data rather than printing as we go.
    """

    if not turn.executions:
        return

    print("\n" + "-" * 52)
    print(f"Tool executions ({turn.rounds} agent rounds)")
    print("-" * 52)

    for record in turn.executions:
        print(f"  {record.summary_line()}")

    failed = turn.failed_executions

    print(
        f"\n  {len(turn.executions)} calls, "
        f"{len(failed)} failed, "
        f"{turn.total_tool_ms:.0f}ms in tools"
    )

    for record in failed:
        if record.error is not None:
            print(f"    {record.tool_name}: {record.error.message}")


def print_token_usage(turn: AgentTurn) -> None:
    """
    What one finished task cost in tokens.

    Printed for EVERY turn, unlike the timeline - a turn with no tool
    calls still spends tokens, and those are the cheap turns you want to
    compare the expensive ones against.

    Read it like this:

      in   - everything the model had to read. Re-sent in full on every
             round, so a 5-round turn pays for the system prompt, the
             history AND every earlier tool result five times over. This
             is the number that grows fastest, and trimming verbose tool
             output is what shrinks it.
      out  - what the model generated: its thinking, its tool calls and
             the final answer.
    """

    print("\n" + "-" * 52)
    print("Token usage")
    print("-" * 52)

    for round_number, prompt_tokens, eval_tokens in turn.token_rounds:
        print(
            f"  round {round_number:<3} "
            f"in {prompt_tokens:>7,}   "
            f"out {eval_tokens:>6,}"
        )

    print(
        f"\n  TOTAL      in {turn.prompt_tokens:>7,}   "
        f"out {turn.eval_tokens:>6,}   "
        f"= {turn.total_tokens:,} tokens "
        f"over {turn.rounds} round(s)"
    )


async def preflight_ollama() -> bool:
    """
    Verify the configured model actually answers before the REPL starts.

    Without this, a bad model name or a missing cloud login only shows
    up on the first message - and it surfaces as a 100-line nested
    ExceptionGroup, because the failure crosses two anyio TaskGroups
    (stdio_client and ClientSession) on its way out. Each one re-wraps
    it, so the one line that matters ends up buried at the bottom.

    Checking here turns that into one actionable sentence.
    """

    model = settings.ollama_model

    try:
        await AsyncClient().chat(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True

    except ResponseError as exc:

        print(f"\nOllama rejected model '{model}' "
              f"(HTTP {exc.status_code}).")

        if exc.status_code == 401:
            # ':cloud' models are proxied by the local daemon to
            # ollama.com, which needs an account. A fresh machine has
            # the daemon but not the login.
            print("\n  This model runs on Ollama Cloud and this "
                  "machine is not signed in.")
            print("\n  Fix it with:")
            print("      ollama signin")
            print("\n  Or point OLLAMA_MODEL in .env at a local "
                  "model that supports tools:")
            print("      ollama list          # what you already have")
            print("      ollama show <model>  # needs 'tools' under "
                  "Capabilities")

        elif exc.status_code == 404:
            print(f"\n  Not downloaded. Fix it with:")
            print(f"      ollama pull {model}")

        return False

    except Exception as exc:
        print(f"\nCould not reach Ollama at 127.0.0.1:11434 - {exc}")
        print("\n  Is the daemon running?")
        print("      ollama serve")
        return False


async def main() -> None:

    # sys.executable, not "python".
    #
    # Windows ships a `python` shim, so the literal worked there.
    # Ubuntu ships only `python3` - a bare "python" resolves solely
    # when a venv happens to be activated, so the client broke the
    # moment it was launched by absolute path, from an IDE, or by a
    # supervisor.
    #
    # sys.executable is the interpreter already running this file, so
    # the server subprocess is guaranteed the same venv and the same
    # installed dependencies.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["server.py"],
    )

    async with stdio_client(server_params) as (read, write):

        async with ClientSession(read, write) as session:

            await session.initialize()

            engine = AgentEngine(
                server_name="personal-mcp-server",

                # Reads run automatically; anything that writes,
                # deletes or changes sharing asks first
                # (AI_Agent_Hub.md section 14).
                approval=ConsoleApproval(),
            )

            await engine.discover_tools(session)

            print_startup_summary(engine)

            # Fail loudly here rather than on the user's first
            # message, where the error is unreadable.
            if not await preflight_ollama():
                return

            messages = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                }
            ]

            # Services used by the previous turn.
            #
            # Carried forward so a follow-up like "and delete that one"
            # or "show me more" still routes to the right service even
            # though it names none itself.
            previous_namespaces: tuple[str, ...] = ()

            while True:

                try:
                    user_message = input("\nYou: ").strip()

                except (EOFError, KeyboardInterrupt):
                    break

                if not user_message:
                    continue

                if user_message.lower() in {"exit", "quit"}:
                    print("\nGoodbye!")
                    break

                # ---------------------------------------------------
                # Route
                # ---------------------------------------------------

                decision = engine.route(
                    user_message,
                    previous_namespaces=previous_namespaces or None,
                )

                print_routing(decision)

                # Only remember confident decisions. Carrying a
                # fallback forward would pin the conversation to a
                # service the router was never sure about.
                if not decision.fallback_used and decision.namespaces:
                    previous_namespaces = decision.namespaces

                selected_mcp_tools = engine.select_mcp_tools(decision)

                # ---------------------------------------------------
                # Agent
                # ---------------------------------------------------

                def widen(
                    already_offered: set[str],
                    _query: str = user_message,
                ) -> list:
                    """
                    Second-chance retrieval.

                    Called by the loop when every tool in a round
                    failed. Re-routes the SAME question with the failed
                    tools excluded, so the model gets a genuinely
                    different set instead of the same dead end.

                    `_query` is bound as a default argument on purpose:
                    it captures this turn's message by value, so the
                    closure cannot drift onto a later one.
                    """

                    return engine.select_mcp_tools(
                        engine.route(
                            _query,
                            exclude=already_offered,
                            top_k=8,
                        )
                    )

                print("\n[Agent] Executing...")

                try:
                    turn = await run_agent(
                        session=session,
                        mcp_tools=selected_mcp_tools,
                        user_message=user_message,
                        messages=messages,
                        executor=engine.executor,
                        escalate=widen,
                    )

                except ResponseError as exc:
                    # Let the session survive a model-side failure.
                    # Raising here would unwind through both anyio
                    # TaskGroups and tear down the MCP connection for
                    # a problem that is usually transient.
                    print(f"\nModel error (HTTP {exc.status_code}): "
                          f"{exc.error}")

                    if exc.status_code == 401:
                        print("  Session expired - run: ollama signin")

                    continue

                print_timeline(turn)

                print_token_usage(turn)

                print(f"\nAI: {turn.answer}")


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nGoodbye!")
