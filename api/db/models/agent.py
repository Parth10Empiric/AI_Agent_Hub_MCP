from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import LAZY_RAISE, Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from api.db.models.conversation import Conversation
    from api.db.models.user import User


class Agent(UUIDMixin, TimestampMixin, Base):
    """
    A configured assistant: a prompt, a model, and a set of tools.
    """

    __tablename__ = "agents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(String(512))

    # Text, not String(n). System prompts grow, and guessing a limit
    # now means a migration the first time someone writes a long one.
    # PostgreSQL stores both identically - the length limit is a
    # constraint, not an optimisation.
    system_prompt: Mapped[str] = mapped_column(Text)

    model: Mapped[str] = mapped_column(String(120))

    temperature: Mapped[float] = mapped_column(
        Float,
        default=0.7,
        server_default="0.7",
    )

    # Soft delete. Conversations and executions reference this row, and
    # users delete things by accident. Archiving keeps the history
    # readable; a hard delete would either destroy it or leave dangling
    # references.
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )

    user: Mapped["User"] = relationship(
        back_populates="agents",
        lazy=LAZY_RAISE,
    )

    tools: Mapped[list["AgentTool"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        lazy=LAZY_RAISE,
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        lazy=LAZY_RAISE,
    )


class AgentTool(UUIDMixin, Base):
    """
    One tool this agent may use, with the user's overrides.

    The defaults come straight from Phase 2 classification:

        enabled           = tool.read_only
        requires_approval = tool.requires_approval

    So a new agent can read but not write, and the user opts in to
    writes deliberately. That is the correct direction for a product
    holding someone's real GitHub account.

    No TimestampMixin: this is a settings row, not an event. created_at
    on it would never be read.
    """

    __tablename__ = "agent_tools"

    __table_args__ = (
        UniqueConstraint("agent_id", "tool_name"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        index=True,
    )

    # A tool NAME, not a foreign key. Tools live in the MCP server and
    # are discovered at startup - there is no tools table to point at,
    # and there should not be: adding a service to the MCP server must
    # not require a database migration.
    tool_name: Mapped[str] = mapped_column(String(120))
    namespace: Mapped[str] = mapped_column(String(64))

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
    )

    # Overrides the Phase 2 default for this agent. A user who trusts
    # an agent can switch approval off for a write tool; a cautious one
    # can switch it on for a read.
    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )

    agent: Mapped["Agent"] = relationship(
        back_populates="tools",
        lazy=LAZY_RAISE,
    )
