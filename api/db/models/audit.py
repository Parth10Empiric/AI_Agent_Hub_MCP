from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Uuid, desc, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, UUIDMixin


class AuditLog(UUIDMixin, Base):
    """
    Everything that happened, in one append-only trail.

    ONE TABLE, NOT SEVERAL, AND THAT IS THE WHOLE DESIGN

    The query this exists to serve is asked at 2am, under pressure, by
    somebody who has never seen the schema:

        "Show me everything that happened to this agent, in order."

    Across two tables that is a UNION with hand-aligned columns. Across
    one it is a WHERE and an ORDER BY. This table was `permission_audit`
    until Phase 5.8 and covered only scope changes; widening it beat
    adding a second table beside it, for exactly that reason.

    INSERT ONLY. NO UPDATE, NO DELETE.

    Not enforced by the ORM - enforced by the DATABASE ROLE, because a
    rule the application imposes on itself is a rule an SQL injection
    in the application does not have to obey:

        REVOKE UPDATE, DELETE ON audit_log FROM agenthub_app;

    See scripts/setup_db_roles.py. Until that role exists this is a
    convention, and a convention is not a control.

    WHY THE IDs ARE PLAIN COLUMNS AND NOT FOREIGN KEYS

    They point at rows that can be deleted. A cascading foreign key
    would mean deleting an agent erases the evidence of what that agent
    was allowed to do - so deleting the evidence becomes a step in the
    attack.

    An audit row must outlive its subject. A dangling id in a log is
    readable; an absent log is not.
    """

    __tablename__ = "audit_log"

    __table_args__ = (
        # "What happened to this thing?" - the resource timeline.
        Index(
            "ix_audit_log_resource",
            "resource_type",
            "resource_id",
            desc("occurred_at"),
        ),
        # "What did this person do?" - the actor timeline, which is
        # the one an investigation starts from.
        Index("ix_audit_log_actor", "actor_user_id", desc("occurred_at")),
        # "Everything in this request" - see request_id below.
        Index("ix_audit_log_request", "request_id"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )

    # The TENANT this event belongs to. Not necessarily who did it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)

    # WHO did it. NULL for events with no human behind them - an
    # approval that expired, a token refreshed by a background job.
    #
    # Separate from user_id because they diverge the moment anyone else
    # can act on an account, and "a staff member opened this user's
    # data" is the first line a security review looks for.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    # 45 characters is the longest possible IPv6 address.
    actor_ip: Mapped[str | None] = mapped_column(String(45))

    # Ties every row from one HTTP request together, and matches the
    # X-Request-ID header the caller received. A user reporting "it
    # failed at 2:14 and said req_8f3a" hands you their entire request
    # in one query, instead of a timestamp to guess from.
    request_id: Mapped[str | None] = mapped_column(String(32))

    # An api.audit.AuditAction value: "scope.granted", "login.failed".
    action: Mapped[str] = mapped_column(String(48), index=True)

    # What it happened TO. Real columns rather than keys inside
    # metadata, because these are what every query filters on - and a
    # filter on JSON is a filter no index can serve.
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    # Anything else worth knowing, per action.
    #
    # JSONB rather than a nullable column per event type. With seven
    # kinds of event, `scope` and `tool_name` columns were right; with
    # twenty-six, they would be twenty-six mostly-empty columns.
    #
    # IDENTIFIERS, NEVER CONTENT. Which file, not the file; which
    # channel, not the message. Append-only plus twelve months of
    # retention means anything recorded here is something you have
    # committed to keeping - and a copy of your users' Slack messages
    # that you have forbidden yourself from deleting is not an audit
    # log, it is a liability.
    #
    # The attribute is `meta` because `metadata` is taken:
    # DeclarativeBase.metadata is the schema object SQLAlchemy itself
    # uses, and shadowing it breaks the mapper with a confusing error.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default="{}",
    )

    # No TimestampMixin: it would add updated_at, and an updated_at on
    # an append-only table is an invitation.

    def __repr__(self) -> str:
        return (
            f"<AuditLog {self.action} "
            f"{self.resource_type}:{self.resource_id}>"
        )
