from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import LAZY_RAISE, Base

if TYPE_CHECKING:
    from api.db.models.conversation import Message


class ToolExecution(Base):
    """
    One tool call, start to finish.

    This table maps 1:1 onto ExecutionRecord.to_dict() from Phase 2.8.
    That is the payoff for recording telemetry as structured data
    instead of printing it: persisting a turn needs no mapping code.

        for record in turn.executions:
            db.add(ToolExecution(**record.to_dict(), message_id=msg.id))

    It is also the data behind the timeline panel in the UI and the
    tool_start / tool_end SSE events.
    """

    __tablename__ = "tool_executions"

    __table_args__ = (
        Index("ix_executions_user_started", "user_id", desc("started_at")),

        # A PARTIAL index: only failed rows are indexed.
        #
        # The executions page is almost always filtered to "what
        # broke", and failures are a small fraction of all rows. This
        # index stays tiny and is read constantly. Indexing every
        # status would mostly store the answer to a question nobody
        # asks.
        Index(
            "ix_executions_status",
            "status",
            postgresql_where=text("status <> 'success'"),
        ),
    )

    # NOT a UUID, and not generated here.
    #
    # This is ExecutionRecord.execution_id - "exec_7417a3c40fa2" -
    # created by the executor before any database is involved. It is
    # already in the SSE event and the console timeline.
    #
    # If this table minted its own id, one event would have two ids and
    # the id the user sees would not be the id stored. An identifier
    # belongs to whatever creates the thing.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Nullable: executions are persisted alongside the assistant
    # message, and it is convenient to write them before that row
    # exists.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
    )

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
    )

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
    )

    # Denormalised for the same reason as conversations.user_id: the
    # executions page filters by user, and this avoids a three-table
    # join on a busy read.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )

    # --- straight from ToolDefinition -------------------------------

    tool_name: Mapped[str] = mapped_column(String(120))
    server: Mapped[str] = mapped_column(String(64))
    namespace: Mapped[str | None] = mapped_column(String(64))

    # read | write | delete | admin
    operation: Mapped[str] = mapped_column(String(16))

    # safe | low | medium | high | critical
    risk_level: Mapped[str] = mapped_column(String(16))

    # --- what happened ----------------------------------------------

    # success | failed | denied
    #
    # DENIED is deliberately distinct from FAILED. "The user said no"
    # is the system working correctly; "GitHub returned a 500" is an
    # incident. Merging them would make the error dashboard lie.
    status: Mapped[str] = mapped_column(String(16))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=1)

    # Already redacted by Phase 2 - redact_arguments() replaces secrets
    # with ***redacted*** and truncates long text where the record is
    # BUILT, not where it is printed. So this column cannot contain a
    # token, and cannot become a copy of the user's Slack messages.
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # "page_size: str -> integer". Recorded rather than silent: the
    # executor edited what the model asked for, and that belongs in the
    # timeline.
    coercions: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default="{}",
    )

    # Three states, which is why it is not a plain bool:
    #   True  - a human approved it
    #   False - a human refused
    #   NULL  - nobody was asked (a read, or approval was off)
    approved_by_user: Mapped[bool | None] = mapped_column(Boolean)

    # ToolError.to_dict(), or NULL on success.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    message: Mapped["Message | None"] = relationship(
        back_populates="executions",
        lazy=LAZY_RAISE,
    )
