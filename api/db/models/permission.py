from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import LAZY_RAISE, Base, UUIDMixin

if TYPE_CHECKING:
    from api.db.models.agent import Agent


class AgentScope(UUIDMixin, Base):
    """
    One coarse permission granted to one agent.

    THIS TABLE IS "WHAT IS TRUE NOW", NOT "WHAT HAPPENED".

    Revoking deletes the row. That sounds lossy, and it would be if
    this were the only table - but every grant and revoke also writes a
    PermissionAudit entry, so the history lives there.

    Keeping current state small and current matters because this table
    is read on the hot path: every single agent turn loads the grant
    set before the executor runs. One index scan on agent_id, a handful
    of short strings, no filtering by "is this one still active".
    """

    __tablename__ = "agent_scopes"

    __table_args__ = (
        # Granting the same scope twice must be a no-op, not a second
        # row. Without this, revoke would delete one row and leave the
        # duplicate behind - the agent would keep the permission the
        # user just took away, which is the worst possible bug in a
        # permissions table.
        UniqueConstraint("agent_id", "scope"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        index=True,
    )

    # "github:issue:write". A string, not a foreign key to a scopes
    # table, for the same reason AgentTool.tool_name is a string: the
    # vocabulary is derived from the live MCP registry at startup
    # (api/scopes.scope_catalog), so adding a service must never
    # require a database migration.
    scope: Mapped[str] = mapped_column(String(120))

    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # WHO granted it. Today always the agent's owner; with team
    # accounts it will not be, and back-filling "who did this" after
    # the fact is impossible.
    #
    # SET NULL rather than CASCADE: if that user is deleted the grant
    # itself must survive, or deleting a colleague's account would
    # silently strip permissions from agents still in use.
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    agent: Mapped["Agent"] = relationship(
        back_populates="scopes",
        lazy=LAZY_RAISE,
    )
