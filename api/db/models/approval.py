from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, UUIDMixin


class PendingApproval(UUIDMixin, Base):
    """
    One tool call, waiting for a human to say yes.

    WHY THIS IS A TABLE AND NOT JUST AN asyncio.Event

    The event is how the waiting turn is WOKEN. This row is what the
    approval IS. Three things need it:

      1. The browser. The dialog is rendered from a GET, possibly in a
         different tab, possibly after a refresh.
      2. The resolving request. POST /approvals/{id}/approve arrives on
         a different connection with a different session, and it has to
         find something to authorise.
      3. The audit. "Who approved sending that Slack message?" must be
         answerable next year, not just next minute.

    THE ROW MUST BE COMMITTED BEFORE THE TURN WAITS

    An uncommitted row does not exist for anybody else. If the turn
    inserted this and then blocked while still inside its transaction,
    the approve request would 404 on a row that is sitting right there
    in another connection's uncommitted state - and the approval could
    never be resolved by anyone.

    See WebApproval.request in api/approvals.py.
    """

    __tablename__ = "pending_approvals"

    __table_args__ = (
        # The approvals inbox: "what is waiting for ME right now".
        # PARTIAL - resolved rows are the overwhelming majority over
        # time and nothing ever queries for them by user alone.
        Index(
            "ix_pending_approvals_waiting",
            "user_id",
            desc("created_at"),
            postgresql_where=text("status = 'pending'"),
        ),

        # No second index on conversation_id: the column below already
        # declares index=True, and two indexes over the same column
        # cost two writes per insert to answer one question.
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )

    # The USER message that started the turn - not the assistant reply,
    # which does not exist yet when this row is written. It is what the
    # UI anchors the dialog to in the thread.
    #
    # SET NULL rather than CASCADE so a deleted message cannot take the
    # record of an approved action with it.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    tool_name: Mapped[str] = mapped_column(String(120))
    operation: Mapped[str] = mapped_column(String(16))
    risk_level: Mapped[str] = mapped_column(String(16))

    # THE REAL ARGUMENTS, with secrets redacted and long values
    # truncated by agent.execution.redact_arguments.
    #
    # Values, not just keys. This is the whole defence against prompt
    # injection: a user who sees
    #
    #     email: attacker@evil.com
    #
    # clicks Cancel. A dialog that says "google_drive_create_permission
    # (3 arguments)" is a confirm button that means nothing, and a
    # trained user will click it every time.
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # pending | approved | denied | expired
    #
    # Text, not a boolean pair. "Expired" is a different story from
    # "denied" - one is a human saying no, the other is a human never
    # seeing the question - and the rate of the second is a number you
    # will want to watch.
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # STORED, not computed from created_at at read time.
    #
    # Two processes are interested in this: the turn that is waiting
    # and the request that resolves it. If each computed "created_at +
    # 5 minutes" against its own clock they could disagree about
    # whether the window is still open, and the disagreement would show
    # up as an approval that executes just after it expired.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    # NULL for an expiry - nobody resolved it, which is exactly the
    # distinction this column exists to record.
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"
