from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from api.schemas.conversation import ExecutionRead


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32_000)


class RoutingSummary(BaseModel):
    """
    What the router decided, for the UI and for tuning.

    fallback_used is the field worth watching. It means the router was
    not confident and widened the tool set instead of narrowing it. A
    rising rate is the lexicon telling you what real users actually
    say - it is a signal, not an error.
    """

    services: list[str] = Field(default_factory=list)
    tool_count: int = 0
    confidence: float = 0.0
    fallback_used: bool = False
    duration_ms: float = 0.0
    unmatched_tokens: list[str] = Field(default_factory=list)


class ApprovalRequired(BaseModel):
    """A call that was refused because nobody could confirm it."""

    tool_name: str
    operation: str
    risk_level: str
    argument_keys: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """
    One completed agent turn.

    The timeline is not decoration. It is how a user understands why an
    answer says what it says, and it is the fastest way to see that a
    tool failed rather than that the model was wrong.
    """

    message_id: uuid.UUID
    conversation_id: uuid.UUID
    answer: str
    created_at: datetime

    rounds: int = 0
    escalations: int = 0
    total_tool_ms: float = 0.0

    routing: RoutingSummary | None = None
    timeline: list[ExecutionRead] = Field(default_factory=list)

    # Non-empty when the agent wanted to do something that needs human
    # confirmation. Phase 5 turns this into a real prompt; today it
    # explains why the agent stopped short.
    approvals_required: list[ApprovalRequired] = Field(default_factory=list)

    # True when older history had to be dropped to fit the model's
    # context window. Surfaced rather than hidden: "the agent forgot
    # something" is a real user-visible behaviour and they deserve to
    # know it happened.
    context_truncated: bool = False
