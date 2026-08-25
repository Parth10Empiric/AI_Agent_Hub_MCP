from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScopeRead(BaseModel):
    """One granted scope, as the UI shows it."""

    model_config = ConfigDict(from_attributes=True)

    scope: str
    granted_at: datetime
    granted_by: uuid.UUID | None


class ScopeOption(BaseModel):
    """
    A scope the user COULD grant, with enough context to decide.

    Built from the live registry, never stored. `tool_count` is what
    makes the choice legible: "github:*:write covers 7 tools" is a
    sentence a user can weigh, where a bare scope string is not.
    """

    scope: str
    service: str
    resource: str
    action: str
    tool_count: int
    granted: bool = False

    # Has the USER connected this service at all?
    #
    # A permission over a service with no account behind it cannot do
    # anything - the tool would be offered, called, and fail at the
    # credential resolver. Listing those scopes alongside the real ones
    # is how a permissions page becomes a wall of choices that do not
    # matter, and a wall is what people click through without reading.
    #
    # Reported rather than filtered out, deliberately. The server does
    # not decide what a client shows: an already-granted scope for a
    # service that was later disconnected MUST stay visible, or a live
    # permission becomes invisible and cannot be revoked.
    connected: bool = False


class AgentScopes(BaseModel):
    """
    GET /api/agents/{id}/scopes

    Returns what IS granted and what COULD be, in one response. Two
    endpoints would mean the UI renders the checkbox list from one
    request and the ticks from another - and briefly shows the wrong
    state whenever the second is slower.
    """

    granted: list[ScopeRead] = Field(default_factory=list)
    available: list[ScopeOption] = Field(default_factory=list)


class ScopeGrant(BaseModel):
    """POST body: the one scope to grant."""

    scope: str = Field(min_length=3, max_length=120)


class PermissionAuditRead(BaseModel):
    """
    One line of the agent's history.

    Reads from AuditLog since Phase 5.8, where the trail widened from
    permission changes to everything. `scope` and `tool_name` used to
    be columns and are now keys in `metadata` - computed here so the
    UI, which only ever wanted those two, did not have to change.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None
    created_at: datetime

    scope: str | None = None
    tool_name: str | None = None
    ip_address: str | None = None

    # Ties this row to the HTTP request that caused it, and to the
    # X-Request-ID the caller was given.
    request_id: str | None = None

    @classmethod
    def from_log(cls, row) -> "PermissionAuditRead":
        meta = row.meta or {}

        return cls(
            id=row.id,
            action=row.action,
            actor_user_id=row.actor_user_id,
            created_at=row.occurred_at,
            scope=meta.get("scope"),
            tool_name=meta.get("tool_name"),
            ip_address=row.actor_ip,
            request_id=row.request_id,
        )


class PermissionAuditPage(BaseModel):
    """
    A cursor-paginated page of audit entries.

    Same shape as MessagePage rather than the generic Page[T], because
    FastAPI generates a clearer OpenAPI schema from a named model than
    from a parametrised generic - and the audit view is one a client's
    security reviewer may well read straight from /docs.
    """

    items: list[PermissionAuditRead]
    next_cursor: str | None = None
    has_more: bool = False
