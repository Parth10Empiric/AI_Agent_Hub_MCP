from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status

from api.deps import CurrentUser, DbDep
from api.pagination import InvalidCursor
from api.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessagePage,
)
from api.services import conversation_service
from api.services.conversation_service import (
    AgentNotFound,
    ConversationNotFound,
)


# TWO routers, because the resources are addressed differently.
#
# Conversations are CREATED and LISTED under an agent - they belong to
# one. Afterwards they are addressed directly by id.
#
# Nesting the id as well, /agents/{aid}/conversations/{cid}, would put a
# value in every URL that is redundant (the conversation already knows
# its agent) and checkable but pointless.

agent_router = APIRouter(prefix="/api/agents", tags=["conversations"])

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _not_found(what: str = "Conversation") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{what} not found.",
    )


@agent_router.get(
    "/{agent_id}/conversations",
    response_model=list[ConversationRead],
)
async def list_conversations(
    agent_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
    include_archived: bool = Query(default=False),
) -> list[ConversationRead]:
    try:
        return await conversation_service.list_conversations(
            session,
            current_user.id,
            agent_id,
            include_archived=include_archived,
        )

    except AgentNotFound:
        raise _not_found("Agent") from None


@agent_router.post(
    "/{agent_id}/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    agent_id: uuid.UUID,
    payload: ConversationCreate,
    current_user: CurrentUser,
    session: DbDep,
) -> ConversationRead:
    try:
        return await conversation_service.create_conversation(
            session, current_user.id, agent_id, payload
        )

    except AgentNotFound:
        raise _not_found("Agent") from None


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
) -> ConversationRead:
    try:
        return await conversation_service.get_conversation(
            session, current_user.id, conversation_id
        )

    except ConversationNotFound:
        raise _not_found() from None


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    current_user: CurrentUser,
    session: DbDep,
) -> ConversationRead:
    """
    Rename or archive.

    Renaming touches updated_at but deliberately NOT last_message_at,
    so the sidebar order does not jump when a user tidies up a title.
    """

    try:
        return await conversation_service.update_conversation(
            session, current_user.id, conversation_id, payload
        )

    except ConversationNotFound:
        raise _not_found() from None


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
) -> Response:
    """
    A real delete. Messages and their tool executions go with it via
    ON DELETE CASCADE.
    """

    try:
        await conversation_service.delete_conversation(
            session, current_user.id, conversation_id
        )

    except ConversationNotFound:
        raise _not_found() from None

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def list_messages(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    session: DbDep,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> MessagePage:
    """
    Newest first, cursor-paginated.

    Pass next_cursor back to get the following page. The cursor is
    stable across inserts, which OFFSET is not - and this endpoint is
    read while new messages are actively arriving.
    """

    try:
        return await conversation_service.list_messages(
            session,
            current_user.id,
            conversation_id,
            limit=limit,
            cursor=cursor,
        )

    except ConversationNotFound:
        raise _not_found() from None

    except InvalidCursor:
        # 422, not 404: the client sent something malformed, which is
        # different from asking for a page that does not exist.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor.",
        ) from None
