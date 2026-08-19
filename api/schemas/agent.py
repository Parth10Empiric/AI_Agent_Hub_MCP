from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentToolRead(BaseModel):
    """
    One tool as configured on one agent.

    Combines two sources:

      - the agent's stored row      enabled, requires_approval
      - Phase 2 classification      operation, risk_level, description

    The classification half is NOT stored in the database. It is read
    from the live registry on every request, so a tool that gets
    reclassified - or removed from the MCP server - is reflected
    immediately instead of showing whatever was true when the agent was
    created.
    """

    model_config = ConfigDict(from_attributes=True)

    tool_name: str
    namespace: str
    enabled: bool
    requires_approval: bool

    # From the registry. None when the agent has a row for a tool the
    # MCP server no longer exposes.
    description: str | None = None
    operation: str | None = None
    risk_level: str | None = None
    available: bool = True


class AgentToolWrite(BaseModel):
    """One tool setting, as a client sends it."""

    enabled: bool = True

    # None means "use the Phase 2 default for this tool", rather than
    # silently choosing False. The distinction matters: a client that
    # omits the field wants the safe default, not "no approval".
    requires_approval: bool | None = None


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=512)

    system_prompt: str = Field(min_length=1, max_length=20_000)

    model: str = Field(default="minimax-m3:cloud", max_length=120)

    # 0 = deterministic, 2 = wild. Bounded because the value is passed
    # straight to the model provider, and an out-of-range number is an
    # error from the provider rather than from us - much harder for a
    # user to understand.
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    # Which services this agent may draw tools from. Empty means all
    # currently connected services.
    plugins: list[str] = Field(default_factory=list)

    # Optional per-tool overrides. Anything omitted gets the Phase 2
    # default: enabled if the tool is read-only.
    tools: dict[str, AgentToolWrite] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """
    A PATCH body: every field optional.

    None means "leave unchanged", which is why nothing here has a
    non-None default. Distinguishing "not sent" from "set to null"
    is the whole difference between PATCH and PUT.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=512)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    model: str | None = Field(default=None, max_length=120)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    is_archived: bool | None = None


class AgentSummary(BaseModel):
    """An agent in a list. No tools - that would be an N+1 waiting."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    avatar_url: str | None
    model: str
    temperature: float
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    # Computed, not columns.
    tool_count: int = 0
    namespaces: list[str] = Field(default_factory=list)


class AgentDetail(AgentSummary):
    system_prompt: str
    tools: list[AgentToolRead] = Field(default_factory=list)


class AgentToolsUpdate(BaseModel):
    """Body for PUT /agents/{id}/tools - the complete desired set."""

    tools: dict[str, AgentToolWrite]
