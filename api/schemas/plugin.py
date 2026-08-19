from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ToolSummary(BaseModel):
    """
    One tool, as the API describes it.

    Every field here is produced by Phase 2 classification. Nothing is
    hand-maintained, so a new tool arrives fully described: what it
    does, how dangerous it is, and whether it will ask before running.
    """

    name: str
    title: str | None
    description: str | None

    # read | write | delete | admin
    operation: str

    # safe | low | medium | high | critical
    risk_level: str

    read_only: bool
    requires_approval: bool

    # Two strings per tool: specific and wildcard, e.g.
    # "github:issue:write" and "github:*:write". Phase 5 grants
    # against these.
    permissions: list[str]


class PluginSummary(BaseModel):
    """A service, as it appears in the catalogue list."""

    key: str
    label: str
    description: str
    icon: str
    category: str
    auth_type: str
    docs_url: str | None

    tool_count: int

    # Counts by operation, so the UI can say "18 tools: 12 read,
    # 4 write, 2 admin" without fetching every tool.
    operations: dict[str, int]

    connected: bool
    account_label: str | None = None
    status: str | None = None


class PluginDetail(PluginSummary):
    """A service, plus its full tool list."""

    tools: list[ToolSummary]


class ConnectRequest(BaseModel):
    """
    Connect a service by pasting a credential.

    Phase 5 replaces this with real OAuth. The table does not change -
    only what fills credentials_enc.
    """

    # The maximum matters: this value is encrypted and stored, and an
    # unbounded field on an authenticated endpoint is still a way to
    # fill a database.
    credential: str = Field(min_length=8, max_length=4096)

    # Shown in the UI so a user can tell two accounts apart. Never the
    # credential itself.
    account_label: str | None = Field(default=None, max_length=255)

    scopes: list[str] = Field(default_factory=list)


class ConnectionRead(BaseModel):
    """
    A user's connection to one service.

    An ALLOW-LIST, exactly like UserRead. credentials_enc is not here
    and must never be: returning it - even encrypted - hands an
    attacker the ciphertext to work on offline, and costs the user
    nothing to have withheld.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plugin_key: str
    status: str
    account_label: str | None
    scopes: list[str]
    connected_at: datetime | None
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
