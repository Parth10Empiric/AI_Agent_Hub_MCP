from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEntryRead(BaseModel):
    """
    One line of the user's own activity.

    NOT the raw row. `label` is written for a person - "Granted
    github:*:write" rather than "scope.granted" - because this screen
    exists so a non-engineer can answer "did anything happen to my
    account that I did not do?"
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str

    # Plain English, computed server-side. Doing it in the browser
    # would mean the vocabulary lives in two places and drifts.
    label: str
    category: str

    resource_type: str | None = None
    resource_id: uuid.UUID | None = None

    actor_ip: str | None = None
    request_id: str | None = None
    occurred_at: datetime

    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    items: list[AuditEntryRead]
    next_cursor: str | None = None
    has_more: bool = False
