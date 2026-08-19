from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine
from agent.executor import ToolExecutor
from agent.loop import AgentTurn, run_agent

from api.approvals import WebApproval
from api.context import build_llm_messages
from api.db.models import Agent, AgentTool, Conversation, Message, ToolExecution
from api.mcp.provider import MCPSessionProvider
from api.mcp.scoped import ScopedSession
from api.policies import AgentToolPolicy
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


# How many stored messages to consider before the token budget trims
# further. A ceiling on the QUERY, not on the context.
HISTORY_LIMIT = 200


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
) -> tuple[Conversation, Agent, set[str]]:
    """
    Load everything the turn needs, with ownership enforced.

    Three things, three queries, no lazy loading anywhere - every
    relationship is lazy="raise_on_sql", so an accidental N+1 would
    raise here rather than quietly costing a query per tool.
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

    # Names only - the policy needs a membership test, nothing more.
    enabled = set(
        await session.scalars(
            select(AgentTool.tool_name).where(
                AgentTool.agent_id == agent.id,
                AgentTool.enabled.is_(True),
            )
        )
    )

    if not enabled:
        raise AgentUnavailable("This agent has no tools enabled.")

    return conversation, agent, enabled


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

    conversation, agent, enabled = await _load_turn_context(
        session, user_id, conversation_id
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
    # Two independent filters. Routing is advisory and a clever prompt
    # can influence what looks relevant; the enabled set is consent,
    # recorded by the user in the agent's configuration.
    mcp_tools = [
        tool
        for tool in engine.select_mcp_tools(decision)
        if tool.name in enabled
    ]

    # --- 6. run -----------------------------------------------------
    approval = WebApproval()

    executor = ToolExecutor(
        engine.registry,
        # Denies by default: a tool the user never granted cannot be
        # called, no matter what the model asks for or why.
        policy=AgentToolPolicy(enabled, agent.name),
        approval=approval,
    )

    def widen(already_offered: set[str], _query: str = content) -> list:
        """
        Second-chance retrieval, restricted to allowed tools.

        The loop calls this when every tool in a round failed. Without
        the `enabled` filter it would happily widen into tools this
        agent was never granted, and the executor would then refuse
        every one of them - wasting a whole round.
        """

        return [
            tool
            for tool in engine.select_mcp_tools(
                engine.route(_query, exclude=already_offered, top_k=8)
            )
            if tool.name in enabled
        ]

    turn: AgentTurn = await run_agent(
        # NOT the raw session. ScopedSession takes the shared MCP lock
        # per tool call instead of for the whole turn, so one user's
        # 30-second conversation does not block everyone else.
        session=ScopedSession(provider, str(user_id)),
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
