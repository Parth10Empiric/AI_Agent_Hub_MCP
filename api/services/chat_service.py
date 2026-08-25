from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine
from agent.execution import ExecutionStatus
from agent.executor import ToolExecutor
from agent.loop import AgentTurn, run_agent

from api.approvals import (
    DEFAULT_TIMEOUT_SECONDS,
    DeferredApproval,
    WebApproval,
)
from api.context import build_llm_messages, wants_fresh_data
from api.db.models import (
    Agent,
    AgentScope,
    AgentTool,
    Conversation,
    Message,
    PluginConnection,
    ToolExecution,
)
from api.anomaly import detector
from api.budgets import AgentBudget, key
from api.ratelimit import RateLimiter
from api.credentials import CredentialStore
from api.mcp.credentials import build_resolver
from api.mcp.provider import MCPSessionProvider
from api.mcp.scoped import ScopedSession
from api.notifier import ApprovalNotifier
from api.policies import DatabaseScopePolicy
from api.settings import APISettings
from api.schemas.chat import (
    ApprovalRequired,
    ChatResponse,
    RoutingSummary,
)
from api.schemas.conversation import ExecutionRead
from api.services import agent_service, conversation_service
from api.services.conversation_service import ConversationNotFound


class ChatError(Exception):
    """Base for chat failures."""


class AgentUnavailable(ChatError):
    """The conversation's agent is archived, or has no tools enabled."""


class TurnLimitReached(ChatError):
    """
    This user has run too many agent turns this hour.

    Carries `retry_after` so the router can send a real Retry-After
    header and the UI can count down, rather than telling someone to
    "try again later" and leaving them to guess.
    """

    def __init__(self, message: str, *, retry_after: int = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# How many stored messages to consider before the token budget trims
# further. A ceiling on the QUERY, not on the context.
HISTORY_LIMIT = 200


@dataclass(slots=True)
class TurnContext:
    """
    Everything one turn needs, loaded once, with ownership proved.

    A dataclass rather than a five-item tuple. The tuple was fine at
    three; at five, `_, _, enabled, granted, overrides = ...` is a bug
    waiting for someone to insert a field in the middle.
    """

    conversation: Conversation
    agent: Agent

    # Which tools are switched on (Phase 3).
    enabled: set[str]

    # Which scopes are granted (Phase 5.1).
    granted: set[str]

    # Which tools this agent wants confirmed (Phase 5.2).
    approval_overrides: dict[str, bool]


def _started_at(record) -> datetime:
    """
    ExecutionRecord.started_at is an ISO STRING, not a datetime.

    Phase 2 stores it that way deliberately - the record is designed to
    be serialised straight to JSON for logs and SSE frames. The
    database column is timestamptz, so it has to be parsed on the way
    in. Always UTC, because utc_now_iso() guarantees it.
    """

    return datetime.fromisoformat(record.started_at)


async def _load_turn_context(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> TurnContext:
    """
    Load everything the turn needs, with ownership enforced.

    Four things, four queries, no lazy loading anywhere - every
    relationship is lazy="raise_on_sql", so an accidental N+1 would
    raise here rather than quietly costing a query per tool.

    The last two - enabled tool names and granted scopes - are the
    permission model, read ONCE here rather than inside the executor.
    That keeps the check itself pure CPU, and fixes the permissions for
    the whole turn so a grant cannot change halfway through a
    multi-tool answer.
    """

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )

    if conversation is None:
        raise ConversationNotFound(str(conversation_id))

    agent = await session.scalar(
        select(Agent).where(
            Agent.id == conversation.agent_id,
            Agent.user_id == user_id,
        )
    )

    if agent is None:
        raise ConversationNotFound(str(conversation_id))

    if agent.is_archived:
        raise AgentUnavailable("This agent has been archived.")

    # Pick up tools that shipped after this agent was created.
    #
    # Without this the turn would be routed over a frozen catalogue:
    # the agent holds "github:*:read", the user asks about releases,
    # and github_list_releases is simply not in the candidate set
    # because no row exists for it. The model then explains it has no
    # such tool, which reads as a capability gap rather than a stale
    # table. Idempotent, and a no-op once the agent is current.
    await agent_service.sync_agent_tools(session, engine, [agent.id])

    # Two things from one query: which tools are on, and which of them
    # this agent wants a human to confirm.
    #
    # PHASE 5.2: that second column has existed since Phase 3 and was
    # never read - the executor used the Phase 2 CLASSIFICATION
    # instead, so the user's own setting did nothing in either
    # direction. It is loaded here and handed to the approval handler,
    # which is where consent belongs.
    tool_rows = list(
        await session.execute(
            select(AgentTool.tool_name, AgentTool.requires_approval).where(
                AgentTool.agent_id == agent.id,
                AgentTool.enabled.is_(True),
            )
        )
    )

    enabled = {name for name, _ in tool_rows}
    approval_overrides = {name: needs for name, needs in tool_rows}

    if not enabled:
        raise AgentUnavailable("This agent has no tools enabled.")

    # PHASE 5.1: the coarse grants. Scalars only - the policy needs a
    # set intersection, nothing more.
    granted = set(
        await session.scalars(
            select(AgentScope.scope).where(AgentScope.agent_id == agent.id)
        )
    )

    # An agent with tools ticked but no scope granted can do nothing,
    # and would spend a full turn having every call denied. Saying so
    # up front is both cheaper and far easier to act on than an answer
    # that explains it failed six times.
    if not granted:
        raise AgentUnavailable(
            "This agent has no permissions granted. "
            "Grant a scope such as 'github:*:read' first."
        )

    return TurnContext(
        conversation=conversation,
        agent=agent,
        enabled=enabled,
        granted=granted,
        approval_overrides=approval_overrides,
    )


async def _credentials_changed_at(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> datetime | None:
    """
    When this user last connected, reconnected or revoked a service.

    PHASE 5.8. Every tool result stored before that moment was fetched
    with credentials that are no longer in use, so it may describe a
    completely different account - and nothing in the conversation says
    so, because reconnecting happens on the settings page, not in the
    chat.

    From a real session: the user swapped their GitHub token, asked for
    their repositories, and the agent answered from a list fetched
    under the OLD token without calling anything. It then "explained"
    the difference by inventing a repository, because the user had said
    the token changed and an answer that agrees is more probable than
    one that admits it did not look.

    One MAX over a small indexed table, once per turn. The alternative
    - a cache keyed by credential version - is faster and has one more
    thing that can be stale, which is the bug being fixed.
    """

    return await session.scalar(
        select(func.max(PluginConnection.updated_at)).where(
            PluginConnection.user_id == user_id
        )
    )


# How many blocked capabilities to name. Enough to be actionable, few
# enough that the notice cannot become the longest thing in the prompt.
MAX_BLOCKED_REPORTED = 3

BLOCKED_NOTICE = """\
[runtime notice] These tools match this request but were withheld from \
you: {blocked}. They EXIST on this server - you are simply not \
permitted to use them right now. Do not tell the user the capability \
does not exist or that no such tool exists. If you cannot answer \
without them, say plainly which permission is missing and that they \
can grant it in the agent's settings.\
"""


async def _blocked_capabilities(
    engine: AgentEngine,
    content: str,
    previous: tuple[str, ...],
    policy: DatabaseScopePolicy,
    enabled: set[str],
    decision,
) -> list[str]:
    """
    Relevant tools this agent was NOT allowed to be offered.

    THE FAILURE THIS COMES FROM

        user   "list out all github repo name"
        agent  "I don't have a tool available that can list all GitHub
                repositories... none of them can enumerate the list of
                repos under your account."

    Every word of that was true from where the model was standing, and
    completely misleading. `github_list_repositories` exists, is
    switched on for that agent, and was ranked third by the router. It
    was removed before the model ever saw it, because the agent had
    been granted `github:*:admin`, `github:repository:write` and
    `github:workflow_run:read` - and no read scope for repositories.

    So the user reads "this product cannot list repositories" when the
    truth is "you have not ticked one box". They then debug the router,
    which is working perfectly.

    THE RULE THIS RESTORES

    A permission system must be able to say NO OUT LOUD. Silently
    removing a capability and letting the agent improvise an
    explanation is the worst of both: the user is not told what to fix,
    and the model invents a reason - which is how "I have no tool for
    that" becomes a statement about the product rather than about a
    checkbox.

    HOW RELEVANCE IS DECIDED

    By routing again over the tools that FAILED the policy, and keeping
    the ones that outscore the weakest tool that was actually offered.
    That is a precise definition of what was lost: "this would have
    been in your toolset if it were permitted". It reuses the ranking
    the router already does instead of inventing a second, weaker
    notion of relevance that could drift from it.

    Costs one extra routing pass, roughly 15ms - the query embedding is
    already cached from the first pass, so no network call.
    """

    # In fallback the router already widened as far as it goes, and
    # every candidate carries score 0.0 - so "outscores the weakest
    # offered tool" would be true of everything.
    if decision.fallback_used or not decision.candidates:
        return []

    floor = min(candidate.score for candidate in decision.candidates)

    blocked = await asyncio.to_thread(
        engine.route,
        content,
        previous_namespaces=previous or None,

        # The exact inverse of the first pass. Rank what was refused.
        allow=lambda definition: not policy.permits(definition),
    )

    reported: list[str] = []

    for candidate in blocked.candidates:

        if len(reported) >= MAX_BLOCKED_REPORTED:
            break

        if candidate.tool is None or candidate.score < floor:
            continue

        # WHICH gate refused it, because the two need different
        # sentences from the user's point of view: one is a checkbox on
        # this agent, the other is a permission grant.
        if candidate.tool_name not in enabled:
            reason = "switched off in this agent's tool settings"
        else:
            reason = (
                "needs the permission "
                + " or ".join(candidate.tool.permissions)
            )

        reported.append(f"{candidate.tool_name} ({reason})")

    return reported


async def _previous_namespaces(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> tuple[str, ...]:
    """
    Which services the last turn used.

    This is what makes follow-ups work:

        "show me my github issues"   -> routes to GitHub
        "and close the first one"    -> names NOTHING

    Routed alone, the second message matches nothing and falls back to
    every tool. Carrying the previous turn's services forward at 70%
    strength (the router applies the weighting) keeps it on target.

    Only CONFIDENT turns were stored - see how routing is persisted
    below - because carrying a fallback forward would pin the whole
    conversation to a service the router was never sure about.
    """

    row = await session.scalar(
        select(Message.routing)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.routing.is_not(None),
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )

    if not row:
        return ()

    return tuple(row.get("services") or ())


async def send_message(
    session: AsyncSession,
    engine: AgentEngine,
    provider: MCPSessionProvider,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    on_event: Callable[[str, dict], None] | None = None,
    notifier: ApprovalNotifier | None = None,
    approval_timeout: float | None = None,
    settings: APISettings | None = None,
    store: CredentialStore | None = None,
    limiter: RateLimiter | None = None,
) -> ChatResponse:
    """
    One complete agent turn, persisted.

        1. load conversation + agent + enabled tools   (ownership)
        2. persist the user message
        3. rebuild history within the token budget
        4. route
        5. INTERSECT routing with what the agent may use
        6. run the turn
        7. persist the assistant message
        8. persist every ExecutionRecord
        9. return answer + timeline

    Step 5 is the new idea in this phase, and it is worth stating
    plainly: the router decides what is RELEVANT, the agent config
    decides what is ALLOWED, and both apply.
    """

    context = await _load_turn_context(
        session, engine, user_id, conversation_id
    )

    conversation = context.conversation
    agent = context.agent
    enabled = context.enabled
    granted = context.granted

    # --- 1b. is there budget for another turn? ----------------------
    #
    # PHASE 5.7, and it goes HERE for one reason: cost.
    #
    # Everything after this line spends money - rebuilding history,
    # routing, and then a model call per round. Checking after any of
    # that would mean the limit stops the answer but not the bill.
    #
    # Before the user's message is stored, too. A turn that never runs
    # should not leave a message in the transcript that was never
    # answered.
    if limiter is not None and settings is not None and settings.rate_limit_enabled:

        decision = limiter.check(
            key(user_id, "turns"),
            settings.agent_turns_per_hour,
            3600,
        )

        if not decision.allowed:
            raise TurnLimitReached(
                f"You have used {decision.limit} agent turns in the last "
                f"hour. Try again in about "
                f"{max(1, decision.retry_after // 60)} minutes.",
                retry_after=decision.retry_after,
            )

    # --- 2. the user's message, saved BEFORE anything can fail -------
    #
    # If the LLM times out or a tool explodes, the user's message must
    # still be in their history. Losing what someone typed is the one
    # failure they will never forgive.
    user_message = await conversation_service.add_message(
        session, conversation, role="user", content=content
    )

    # --- 3. history, trimmed to fit ---------------------------------
    history = await conversation_service.history_for_llm(
        session, conversation_id, limit=HISTORY_LIMIT
    )

    # The message just added is the CURRENT input, not history.
    # run_agent appends it itself, and sending it twice would make the
    # model see the question repeated.
    history = [m for m in history if m.id != user_message.id]

    # PHASE 5.8: two reasons to withhold what we already fetched.
    #
    #   the user asked for fresh data   -> drop every stored result
    #   the credentials changed since   -> drop the ones from before
    #
    # Both are decided HERE, not by the model. The model's own
    # judgement is exactly what failed: handed a complete answer with
    # no age on it, re-reading beat re-calling every time. Taking the
    # payload away is a decision it cannot overrule.
    refresh = wants_fresh_data(content)

    built = build_llm_messages(
        agent.system_prompt,
        history,
        drop_tool_results=refresh,
        invalid_before=await _credentials_changed_at(session, user_id),
    )

    # --- 4. route ---------------------------------------------------
    #
    # The policy is built BEFORE routing, not after, because routing
    # now uses it. See `usable` below.
    policy = DatabaseScopePolicy(granted, enabled, agent.name)

    def usable(definition) -> bool:
        """Could this tool actually run for this agent?"""

        return policy.permits(definition)

    previous = await _previous_namespaces(session, conversation_id)

    # IN A THREAD, because routing is no longer purely CPU-bound.
    #
    # `route` is synchronous, and since the semantic layer was switched
    # on it can make one blocking HTTP call to embed the query - about
    # 150ms, on the queries where keyword scoring came up short. Called
    # directly from this async handler that does not slow one turn
    # down; it stalls the event loop, and with it every other user's
    # request, MCP read and SSE heartbeat.
    #
    # The same rule that made agent/loop.py insist on AsyncClient. A
    # synchronous call that only SOMETIMES touches the network is the
    # more dangerous kind, because it behaves perfectly in testing.
    decision = await asyncio.to_thread(
        engine.route,
        content,
        previous_namespaces=previous or None,

        # Spend the tool budget only on tools that could run.
        #
        # This used to be applied to the RESULT instead, and the
        # difference is not cosmetic. A turn asking to file GitHub
        # issues and post a Slack summary pooled Google Drive on the
        # word "file", gave it its guaranteed per-service quota, and
        # then discarded all four of those tools because the agent
        # holds no Drive scope - so the model was handed 12 tools where
        # the budget allowed 16, and the four it lost were the ones it
        # needed.
        allow=usable,
    )

    if on_event is not None:
        on_event(
            "routing",
            {
                "services": list(decision.namespaces),
                "tools": len(decision.candidates),
                "confidence": round(decision.confidence, 3),
                "fallback_used": decision.fallback_used,
            },
        )

    # --- 5. relevant AND allowed ------------------------------------
    #
    # ONE policy object, used for three different jobs:
    #
    #   in step 4  to decide what is worth RANKING      (budget)
    #   here       to decide what to OFFER the model    (efficiency)
    #   in the     to decide what may actually RUN      (security)
    #   executor
    #
    # Building it once means the three can never disagree. If they did,
    # the model would be shown a tool that is refused the moment it
    # asks for it - a whole wasted round and a confusing answer.
    #
    # Note which of them is the control. Routing is advisory and a
    # crafted message can influence what looks relevant; this list is
    # only a suggestion. The gate that holds is inside the executor,
    # below the model, on the tool it actually requested.
    #
    # This second pass is now belt and braces rather than the only
    # check - the router was given the same predicate. It stays because
    # a tool can be in the registry and absent from `_mcp_tools_by_name`
    # after a reconnect, and because a filter that is never wrong is
    # cheap to keep and expensive to have removed by mistake.
    def offerable(mcp_tool) -> bool:
        """Is this tool both enabled and covered by a granted scope?"""

        definition = engine.get_tool(mcp_tool.name)

        return definition is not None and policy.permits(definition)

    mcp_tools = [
        tool
        for tool in engine.select_mcp_tools(decision)
        if offerable(tool)
    ]

    # --- 5b. and say what was withheld ------------------------------
    #
    # A gate that removes a capability silently makes the model invent
    # the reason, and it invents the wrong one - "no such tool exists"
    # rather than "you have not granted me that permission". See
    # `_blocked_capabilities` for the transcript this comes from.
    #
    # This tells the model what it may NOT do. It does not make
    # anything callable: the tools named here are absent from
    # `mcp_tools`, absent from `allowed_tools` in the loop, and would
    # still be refused by the executor if the model asked for one
    # anyway. Naming a locked door is not a key.
    blocked = await _blocked_capabilities(
        engine,
        content,
        previous,
        policy,
        enabled,
        decision,
    )

    turn_messages = list(built.messages)

    if blocked:
        turn_messages.append(
            {
                "role": "user",
                "content": BLOCKED_NOTICE.format(
                    blocked="; ".join(blocked)
                ),
            }
        )

    # --- 6. run -----------------------------------------------------
    #
    # WHICH APPROVAL HANDLER, AND WHY IT DEPENDS ON THE CALLER
    #
    #   notifier given   the caller can hold a connection open for
    #                    minutes (the SSE endpoint) -> suspend and wait
    #
    #   notifier None    the caller cannot (a plain JSON POST, a CLI,
    #                    a scheduled job) -> deny, and report what
    #                    would have needed confirming
    #
    # A plain POST that hangs for five minutes is killed by a proxy,
    # a load balancer or the client's own timeout. Pretending otherwise
    # would trade a clear "this needs your approval" for a mystery
    # 504 - so the non-streaming path keeps the Phase 3 behaviour, on
    # purpose.
    approval = (
        WebApproval(
            session,
            notifier,
            conversation_id=conversation_id,
            agent_id=agent.id,
            agent_name=agent.name,
            user_id=user_id,
            message_id=user_message.id,
            overrides=context.approval_overrides,
            timeout_seconds=(
                approval_timeout
                if approval_timeout is not None
                else DEFAULT_TIMEOUT_SECONDS
            ),
            on_event=on_event,
        )
        if notifier is not None
        else DeferredApproval(context.approval_overrides)
    )

    # PHASE 5.7: the third gate, beside permission and approval.
    #
    #     policy     may this agent ever?     capability
    #     approval   should we, right now?    consent
    #     budget     how many this hour?      volume
    #
    # Volume is invisible to the other two: every call in a mass
    # exfiltration looks exactly like the one the user asked for.
    budget = (
        AgentBudget(limiter, settings, user_id, agent.id)
        if limiter is not None and settings is not None
        else None
    )

    executor = ToolExecutor(
        engine.registry,
        # Denies by default, twice over: a tool the user never enabled,
        # or never granted a scope covering, cannot be called - no
        # matter what the model asks for or why it was asked.
        policy=policy,
        approval=approval,
        budget=budget,
    )

    def widen(already_offered: set[str], query: str = content) -> list:
        """
        Second-chance retrieval, restricted to allowed tools.

        Called two ways, and the difference is who noticed the problem:

          the loop, automatically   every tool in a round failed, so
                                    the first pick could not do the job

          the model, deliberately   it called `find_tools`, because it
                                    has now seen a result and knows
                                    what it needs next

        The second is the important one. Routing happens before the
        turn, from the user's sentence, and no amount of tuning lets it
        predict the tool needed for a step whose ARGUMENTS do not exist
        yet - "read the file that the listing turns up" cannot be
        routed before the listing runs. `query` is what the model asks
        for; it defaults to the user's message for the automatic case,
        which has nobody to write a better one.

        `allow=usable` is not optional in either case. Without the same
        policy filter this would happily widen into tools the agent was
        never granted, and the executor would then refuse every one of
        them - a wasted round, and a model told it has a capability it
        does not. Widening what the model can SEE must never widen what
        it can DO.
        """

        return [
            tool
            for tool in engine.select_mcp_tools(
                engine.route(
                    query,
                    exclude=already_offered,
                    top_k=8,
                    allow=usable,
                )
            )
            if offerable(tool)
        ]

    # PHASE 5.5: whose credentials every tool call is made with.
    #
    # None when the caller did not supply a store - the CLI, the tests,
    # a script. The MCP server then falls back to .env, which is
    # correct in development and refused in production.
    resolver = build_resolver(session, engine, settings, store, user_id)

    turn: AgentTurn = await run_agent(
        # NOT the raw session. ScopedSession takes the shared MCP lock
        # per tool call instead of for the whole turn, so one user's
        # 30-second conversation does not block everyone else.
        session=ScopedSession(provider, str(user_id), resolver=resolver),
        mcp_tools=mcp_tools,
        user_message=content,
        messages=turn_messages,
        executor=executor,
        model=agent.model,
        verbose=False,
        escalate=widen,
        on_event=on_event,
    )

    # --- 7. the assistant message -----------------------------------
    routing_payload = {
        "services": list(decision.namespaces),
        "tool_count": len(decision.candidates),
        "confidence": round(decision.confidence, 4),
        "fallback_used": decision.fallback_used,
        "duration_ms": round(decision.duration_ms, 2),
        "unmatched_tokens": list(decision.unmatched_tokens),
        "rounds": turn.rounds,
        "escalations": turn.escalations,
        "context_truncated": built.truncated,

        # PHASE 5.8. Both are here so that "why did it call the tool
        # again?" and "why did it forget?" are answerable from the
        # stored row, without reconstructing what the user typed.
        "refresh_forced": built.refresh_forced,
        "stale_results_dropped": built.invalidated,
        "blocked_tools": blocked,
        "tool_searches": turn.tool_searches,
    }

    assistant_message = await conversation_service.add_message(
        session,
        conversation,
        role="assistant",
        content=turn.answer,
        routing=routing_payload,
    )

    # --- 8. every execution, success or not -------------------------
    #
    # ExecutionRecord.to_dict() already matches the table, so there is
    # no mapping code here. That is the payoff for building Phase 2.8
    # telemetry as structured data instead of print statements.
    for record in turn.executions:

        data = record.to_dict()

        session.add(
            ToolExecution(
                id=data["execution_id"],
                message_id=assistant_message.id,
                conversation_id=conversation.id,
                agent_id=agent.id,
                user_id=user_id,
                tool_name=data["tool"],
                server=data["server"],
                namespace=data["namespace"],
                operation=data["operation"],
                risk_level=data["risk_level"],
                status=data["status"],
                started_at=_started_at(record),
                duration_ms=data["duration_ms"],
                attempts=data["attempts"],
                arguments=data["arguments"],
                coercions=list(data["coercions"]),
                approved_by_user=data["approved_by_user"],
                error=data["error"],
            )
        )

    await session.flush()

    # --- 9. did this turn look like a burst? ------------------------
    #
    # PHASE 5.6. Mass exfiltration needs volume, and volume is the one
    # thing a per-call permission check cannot see: each call on its
    # own looks exactly like the one the user asked for.
    #
    # AFTER persisting, and it only ever logs. A detector that ran on
    # the hot path, or that refused, would be a rate limiter written by
    # accident - and that is Phase 5.7's job, with a shared window.
    detector.record(
        agent.id,
        (
            r.operation
            for r in turn.executions
            if r.status is ExecutionStatus.SUCCESS
        ),
    )

    return ChatResponse(
        message_id=assistant_message.id,
        conversation_id=conversation.id,
        answer=turn.answer,
        created_at=assistant_message.created_at,
        rounds=turn.rounds,
        escalations=turn.escalations,
        total_tool_ms=round(turn.total_tool_ms, 2),
        routing=RoutingSummary(
            services=list(decision.namespaces),
            tool_count=len(mcp_tools),
            confidence=round(decision.confidence, 4),
            fallback_used=decision.fallback_used,
            duration_ms=round(decision.duration_ms, 2),
            unmatched_tokens=list(decision.unmatched_tokens),
        ),
        timeline=[
            ExecutionRead(
                id=r.execution_id,
                tool_name=r.tool_name,
                namespace=r.namespace,
                operation=r.operation,
                risk_level=r.risk_level,
                status=r.status.value,
                started_at=_started_at(r),
                duration_ms=round(r.duration_ms, 2),
                attempts=r.attempts,
                approved_by_user=r.approved_by_user,
                error=r.error.to_dict() if r.error else None,
            )
            for r in turn.executions
        ],
        # What needed a human and did not get a yes. ONLY refusals -
        # an approved call is not in this list, which is what stopped
        # the chat from printing "nothing was changed in your accounts"
        # under a tool the user had just approved and watched run.
        #
        # Each entry says which refusal it was, because the streaming
        # and non-streaming paths produce different ones: a human
        # denying or letting it expire, versus nobody being there to
        # ask at all.
        approvals_required=[
            ApprovalRequired(
                tool_name=p.tool_name,
                operation=p.operation,
                risk_level=p.risk_level,
                argument_keys=list(p.arguments),
                status=p.status,
            )
            for p in approval.pending
        ],
        context_truncated=built.truncated,
    )
