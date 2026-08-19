from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import LAZY_RAISE, Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from api.db.models.agent import Agent
    from api.db.models.execution import ToolExecution
    from api.db.models.user import User


class Conversation(UUIDMixin, TimestampMixin, Base):
    """
    One chat thread with one agent.
    """

    __tablename__ = "conversations"

    __table_args__ = (
        # The conversation list is always "this agent's threads, newest
        # first". Indexing both columns in that exact order lets
        # PostgreSQL read the answer straight out of the index.
        Index(
            "ix_conversations_agent_last",
            "agent_id",
            desc("last_message_at"),
        ),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
    )

    # Reachable through agent.user_id, and stored anyway.
    #
    # Deliberate denormalisation: the most common query is "all
    # conversations for this user", and every request has to verify
    # ownership. Without this column that check is a join, on the
    # busiest read in the product.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    title: Mapped[str | None] = mapped_column(String(255))

    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )

    # Kept separate from updated_at: renaming a conversation touches
    # updated_at but must not reorder the list.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    agent: Mapped["Agent"] = relationship(
        back_populates="conversations",
        lazy=LAZY_RAISE,
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy=LAZY_RAISE,
    )


class Message(UUIDMixin, Base):
    """
    One turn in a conversation.

    No TimestampMixin: a message is an EVENT. It happened once and is
    never edited, so updated_at would be a column that always equals
    created_at and quietly invites someone to mutate history.
    """

    __tablename__ = "messages"

    __table_args__ = (
        Index("ix_messages_conversation", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
    )

    # system | user | assistant | tool
    role: Mapped[str] = mapped_column(String(16))

    # Nullable: an assistant turn that only calls tools has no text.
    content: Mapped[str | None] = mapped_column(Text)

    # Set only when role='tool'.
    tool_name: Mapped[str | None] = mapped_column(String(120))

    # RoutingDecision.to_dict() from Phase 2, stored verbatim.
    #
    # JSONB rather than JSON: JSONB is parsed once and stored in binary
    # form, so it can be queried and indexed. JSON is stored as text
    # and re-parsed on every read.
    #
    # Kept as a blob rather than exploded into columns because it is
    # DIAGNOSTIC data - read when routing misbehaves, never joined on.
    # Columns would mean a migration every time the router gains a
    # signal.
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
        lazy=LAZY_RAISE,
    )

    executions: Mapped[list["ToolExecution"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        lazy=LAZY_RAISE,
    )
