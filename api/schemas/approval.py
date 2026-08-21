from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApprovalRead(BaseModel):
    """
    One approval, as the dialog renders it.

    `arguments` carries REAL VALUES (secrets redacted, long strings
    truncated). That is the point of the whole screen: a user who reads

        email: attacker@evil.com

    clicks Cancel. A dialog showing only argument NAMES is a confirm
    button that means nothing, and a user trained on meaningless
    confirmations clicks through the one that mattered.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID | None
    agent_id: uuid.UUID

    tool_name: str
    operation: str
    risk_level: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    status: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None = None
    resolved_by: uuid.UUID | None = None

    # Computed, not stored: "how long do I have?" is what the dialog
    # actually shows, and a client that subtracts timestamps itself
    # gets it wrong the moment its clock drifts.
    seconds_remaining: int = 0


class ApprovalPage(BaseModel):
    """A cursor-paginated page of approvals."""

    items: list[ApprovalRead]
    next_cursor: str | None = None
    has_more: bool = False
