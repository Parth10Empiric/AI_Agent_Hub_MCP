from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    # Optional: a conversation created from the chat box has no title
    # until the first message arrives and one is derived from it.
    title: str | None = Field(default=None, max_length=255)


class ConversationUpdate(BaseModel):
    """PATCH: None means leave unchanged."""

    title: str | None = Field(default=None, max_length=255)
    is_archived: bool | None = None


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    title: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    # Separate from updated_at on purpose: renaming a conversation
    # touches updated_at but must NOT reorder the sidebar.
    last_message_at: datetime | None

    # Computed by a correlated subquery, not by loading the messages.
    # Loading them to call len() would be an N+1 across the list.
    message_count: int = 0


class ExecutionRead(BaseModel):
    """
    One tool call in the timeline.

    Every field comes from ExecutionRecord.to_dict(). arguments is
    already redacted by Phase 2 - secrets replaced and long text
    truncated where the record is BUILT - so it is safe to return.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    namespace: str | None
    operation: str
    risk_level: str
    status: str
    started_at: datetime
    duration_ms: float
    attempts: int
    approved_by_user: bool | None
    error: dict[str, Any] | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str | None
    tool_name: str | None
    routing: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    created_at: datetime

    executions: list[ExecutionRead] = Field(default_factory=list)


class MessagePage(BaseModel):
    """
    A cursor-paginated page.

    next_cursor is opaque. A readable ?after_id=5 would invite clients
    to build their own, after which the sort order could never change.
    """

    items: list[MessageRead]
    next_cursor: str | None = None
    has_more: bool = False
