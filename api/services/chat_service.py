from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
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
from api.context import build_llm_messages
from api.db.models import (
    Agent,
    AgentScope,
    AgentTool,
    Conversation,
    Message,
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
from api.services import conversation_service
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

    context = await _load_turn_context(session, user_id, conversation_id)

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

    built = build_llm_messages(agent.system_prompt, history)

    # --- 4. route ---------------------------------------------------
    previous = await _previous_namespaces(session, conversation_id)

    decision = engine.route(content, previous_namespaces=previous or None)

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
    # ONE policy object, used for two different jobs:
    #
    #   here      to decide what to OFFER the model     (efficiency)
    #   in the    to decide what may actually RUN       (security)
    #   executor
    #
    # Building it once means the two can never disagree. If they did,
    # the model would be shown a tool that is refused the moment it
    # asks for it - a whole wasted round and a confusing answer.
    #
    # Note which of the two is the control. Routing is advisory and a
    # crafted message can influence what looks relevant; this list is
    # only a suggestion. The gate that holds is inside the executor,
    # below the model, on the tool it actually requested.
    policy = DatabaseScopePolicy(granted, enabled, agent.name)

    def offerable(mcp_tool) -> bool:
        """Is this tool both enabled and covered by a granted scope?"""

        definition = engine.get_tool(mcp_tool.name)

        return definition is not None and policy.permits(definition)

    mcp_tools = [
        tool
        for tool in engine.select_mcp_tools(decision)
        if offerable(tool)
    ]

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

    def widen(already_offered: set[str], _query: str = content) -> list:
        """
        Second-chance retrieval, restricted to allowed tools.

        The loop calls this when every tool in a round failed. Without
        the same policy filter it would happily widen into tools this
        agent was never granted, and the executor would then refuse
        every one of them - wasting a whole round.
        """

        return [
            tool
            for tool in engine.select_mcp_tools(
                engine.route(_query, exclude=already_offered, top_k=8)
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
        messages=list(built.messages),
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
        # What needed a human. On the streaming path these were all
        # actually asked about; on the non-streaming path they were
        # refused, and this list is how the client finds out why the
        # agent stopped short.
        approvals_required=[
            ApprovalRequired(
                tool_name=p.tool_name,
                operation=p.operation,
                risk_level=p.risk_level,
                argument_keys=list(p.arguments),
            )
            for p in approval.pending
        ],
        context_truncated=built.truncated,
    )
