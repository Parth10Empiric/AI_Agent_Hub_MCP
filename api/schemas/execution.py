from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionListItem(BaseModel):
    """
    One row on the executions page.

    A SEPARATE schema from ExecutionRead, which is the timeline row
    inside a chat. The difference is context: inside a conversation the
    reader already knows which agent is talking, so the timeline does
    not repeat it. The executions page spans every agent, so the agent
    name is the first thing the row has to say.

    Returning ExecutionRead here and "just adding a field later" is how
    one schema ends up serving two screens badly.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    namespace: str | None
    operation: str
    risk_level: str

    # success | failed | denied. Three values, three meanings - see the
    # note in list_executions about why denied is not an error.
    status: str

    started_at: datetime
    duration_ms: float
    attempts: int
    approved_by_user: bool | None
    error: dict[str, Any] | None

    agent_id: uuid.UUID | None
    conversation_id: uuid.UUID | None

    # Joined in, so the page does not have to fetch every agent
    # separately to render a list of names.
    agent_name: str | None = None


class ExecutionPage(BaseModel):
    items: list[ExecutionListItem]
    next_cursor: str | None = None
    has_more: bool = False


class ExecutionStats(BaseModel):
    """
    The dashboard's numbers.

    Computed by the database with GROUP BY, not by fetching rows and
    counting them in Python. A user with 50,000 executions should cost
    one aggregate query, not 50,000 objects loaded into memory.
    """

    window_days: int

    total: int = 0
    success: int = 0
    failed: int = 0
    denied: int = 0

    # Milliseconds. Useful on its own and the input to any "is the
    # agent getting slower" question later.
    avg_duration_ms: float = 0.0

    # tool_name -> count, most used first. Small by construction; a
    # user has at most a few dozen distinct tools.
    by_tool: dict[str, int] = Field(default_factory=dict)
