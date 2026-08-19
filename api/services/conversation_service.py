from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Agent, Conversation, Message
from api.pagination import Cursor, build_page
from api.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessagePage,
    MessageRead,
)


MAX_TITLE_LENGTH = 60


class ConversationError(Exception):
    """Base for conversation failures."""


class ConversationNotFound(ConversationError):
    """
    No such conversation FOR THIS USER.

    Same rule as agents: does-not-exist and belongs-to-someone-else are
    indistinguishable to the caller, so the router answers 404 for both
    and never confirms that an id is real.
    """


class AgentNotFound(ConversationError):
    """No such agent for this user."""


def derive_title(text: str) -> str:
    """
    A title from the first user message.

    No LLM call. Asking a model to summarise would add latency and cost
    to every new conversation for something the user can rename in one
    click - and would do it while they are waiting for their first
    answer.
    """

    cleaned = " ".join(text.split())

    if len(cleaned) <= MAX_TITLE_LENGTH:
        return cleaned

    # Cut at a word boundary when there is one reasonably close, so the
    # title does not end mid-word.
    cut = cleaned[:MAX_TITLE_LENGTH]

    if " " in cut[40:]:
        cut = cut[: cut.rfind(" ")]

    return cut + "..."


# ---------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------


async def _owned_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Conversation:
    """
    Load a conversation, scoped to its owner.

    Filters on conversations.user_id directly - no join to agents.
    That column was denormalised in Phase 3.2 for exactly this: the
    ownership check runs on every single request, and it should not
    cost a join.
    """

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )

    if conversation is None:
        raise ConversationNotFound(str(conversation_id))

    return conversation


async def _owned_agent(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> Agent:

    agent = await session.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
    )

    if agent is None:
        raise AgentNotFound(str(agent_id))

    return agent


# ---------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------


def _message_count_subquery():
    """
    A correlated scalar subquery counting each conversation's messages.

    ONE statement for the whole list. The obvious alternative -
    selectinload(Conversation.messages) then len() - would load every
    message of every conversation into memory to produce a number, and
    a conversation can hold thousands.
    """

    return (
        select(func.count(Message.id))
        .where(Message.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )


async def list_conversations(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[ConversationRead]:

    await _owned_agent(session, user_id, agent_id)

    count = _message_count_subquery()

    stmt = (
        select(Conversation, count.label("message_count"))
        .where(
            Conversation.agent_id == agent_id,
            Conversation.user_id == user_id,
        )
        # Matches ix_conversations_agent_last (agent_id,
        # last_message_at DESC) so PostgreSQL reads the order straight
        # out of the index.
        #
        # NULLS LAST puts a brand-new empty conversation at the bottom
        # rather than the top, where DESC would otherwise sort NULL.
        .order_by(Conversation.last_message_at.desc().nullslast())
    )

    if not include_archived:
        stmt = stmt.where(Conversation.is_archived.is_(False))

    rows = (await session.execute(stmt)).all()

    return [
        ConversationRead(
            **ConversationRead.model_validate(conversation).model_dump(
                exclude={"message_count"}
            ),
            message_count=message_count,
        )
        for conversation, message_count in rows
    ]


async def create_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    payload: ConversationCreate,
) -> ConversationRead:

    await _owned_agent(session, user_id, agent_id)

    conversation = Conversation(
        agent_id=agent_id,
        user_id=user_id,
        title=payload.title,
    )

    session.add(conversation)
    await session.flush()

    return ConversationRead.model_validate(conversation)


async def get_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> ConversationRead:

    conversation = await _owned_conversation(session, user_id, conversation_id)

    count = await session.scalar(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
    )

    read = ConversationRead.model_validate(conversation)
    read.message_count = count or 0

    return read


async def update_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
) -> ConversationRead:

    conversation = await _owned_conversation(session, user_id, conversation_id)

    # exclude_unset is what makes this a PATCH. Without it every
    # omitted field arrives as None and nulls a real value.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(conversation, field, value)

    await session.flush()

    return await get_conversation(session, user_id, conversation_id)


async def delete_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    """
    A HARD delete, unlike agents.

    Agents are archived because conversations and executions point at
    them. A conversation is the leaf: it IS the history, so there is
    nothing left to preserve once the user removes it. ON DELETE
    CASCADE takes the messages and their executions with it.
    """

    conversation = await _owned_conversation(session, user_id, conversation_id)

    await session.delete(conversation)


# ---------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------


async def list_messages(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    limit: int = 20,
    cursor: str | None = None,
) -> MessagePage:
    """
    One page of messages, newest first.

    The UI list: newest first because a chat view opens at the bottom
    and scrolls up. The LLM list, built in api/context.py, is the
    opposite in almost every respect.
    """

    await _owned_conversation(session, user_id, conversation_id)

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        # Tool executions are part of the message. Without
        # selectinload, lazy="raise_on_sql" would refuse - which is the
        # guard working: it is exactly the N+1 that would otherwise run
        # one query per message.
        .options(selectinload(Message.executions))
        .order_by(Message.created_at.desc(), Message.id.desc())
        # limit + 1: the extra row answers "is there more?" without a
        # second COUNT(*) over the whole conversation.
        .limit(limit + 1)
    )

    if cursor:
        position = Cursor.decode(cursor)

        # ROW-VALUE COMPARISON, not two ANDed conditions.
        #
        #     (created_at, id) < (:t, :i)      correct
        #     created_at < :t AND id < :i      WRONG - silently drops
        #                                      every row that shares
        #                                      the timestamp but has a
        #                                      larger id
        stmt = stmt.where(
            tuple_(Message.created_at, Message.id)
            < (position.created_at, position.row_id)
        )

    rows = list(await session.scalars(stmt))

    page, next_cursor, has_more = build_page(
        rows,
        limit,
        key=lambda m: (m.created_at, m.id),
    )

    return MessagePage(
        items=[MessageRead.model_validate(m) for m in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def history_for_llm(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    limit: int = 200,
) -> list[Message]:
    """
    Recent messages in CHRONOLOGICAL order, for api/context.py.

    Fetched newest-first so the limit keeps the most recent messages,
    then reversed - selecting the oldest 200 of a long conversation
    would hand the model ancient history and drop what just happened.

    No ownership check: this is internal, called only after the caller
    has already proved ownership of the conversation.
    """

    rows = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
    )

    rows.reverse()

    return rows


async def add_message(
    session: AsyncSession,
    conversation: Conversation,
    *,
    role: str,
    content: str | None = None,
    tool_name: str | None = None,
    routing: dict | None = None,
    token_usage: dict | None = None,
) -> Message:
    """
    Append a message and move the conversation to the top of the list.

    Titling happens here rather than at creation: a conversation
    created from the chat box has no title until the user actually says
    something.
    """

    message = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        tool_name=tool_name,
        routing=routing,
        token_usage=token_usage,
    )

    session.add(message)

    conversation.last_message_at = datetime.now(timezone.utc)

    if role == "user" and not conversation.title and content:
        conversation.title = derive_title(content)

    await session.flush()

    return message
