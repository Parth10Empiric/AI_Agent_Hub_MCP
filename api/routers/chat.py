from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from api.deps import AgentEngineDep, CurrentUser, DbDep
from api.schemas.chat import ChatRequest, ChatResponse
from api.services import chat_service
from api.services.chat_service import AgentUnavailable
from api.services.conversation_service import ConversationNotFound


router = APIRouter(prefix="/api/conversations", tags=["chat"])


@router.post(
    "/{conversation_id}/messages",
    response_model=ChatResponse,
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: ChatRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
) -> ChatResponse:
    """
    Send a message and run one complete agent turn.

    200, not 201. A resource IS created - the assistant message - but
    the useful thing about this response is the answer and the
    timeline, not a pointer to a new URL. 201 with a Location header
    would be technically defensible and practically unhelpful.

    This endpoint is synchronous: it returns when the turn is finished,
    which can be 5-30 seconds. Phase 3.9 adds SSE so the client can
    watch the tools run instead of staring at a spinner - but the
    non-streaming version stays, because it is what CLI clients, tests
    and scheduled jobs actually want.
    """

    provider = getattr(request.app.state, "mcp_provider", None)

    if provider is None or not provider.healthy:
        # 503, not 500. The request was fine; the MCP subprocess is
        # not. Retrying later may well work, and that is exactly what
        # 503 tells a client.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The tool server is not available right now.",
        )

    try:
        return await chat_service.send_message(
            session,
            engine,
            provider,
            user_id=current_user.id,
            conversation_id=conversation_id,
            content=payload.content,
        )

    except ConversationNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        ) from None

    except AgentUnavailable as exc:
        # 409 Conflict: the request is well-formed but the agent is in
        # a state that cannot serve it - archived, or with every tool
        # switched off.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from None
