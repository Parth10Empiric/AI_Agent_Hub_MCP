from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import LAZY_RAISE, Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from api.db.models.user import User


class PluginConnection(UUIDMixin, TimestampMixin, Base):
    """
    One user's link to one service (github, google_drive, slack...).

    Phase 3 stores a pasted personal access token here. Phase 5
    replaces that with a real OAuth flow - the table does not change,
    only what fills credentials_enc.
    """

    __tablename__ = "plugin_connections"

    __table_args__ = (
        # A user connects each service at most once. Enforced by the
        # DATABASE, not by application code: two concurrent requests
        # can both pass an "already connected?" check and both insert.
        # Only a unique index can actually prevent that.
        UniqueConstraint("user_id", "plugin_key"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    # Matches ToolDefinition.namespace from Phase 2, so the catalogue
    # can be derived from the live registry instead of hardcoded.
    plugin_key: Mapped[str] = mapped_column(String(64))

    # connected | expired | revoked
    #
    # A plain string, not a PostgreSQL ENUM. Adding a value to a native
    # enum needs ALTER TYPE in a migration; removing one is close to
    # impossible. The set of valid values is enforced in application
    # code, where it is easy to change.
    status: Mapped[str] = mapped_column(String(32), default="connected")

    # Shown in the UI: "user@example.com". Never the credential.
    account_label: Mapped[str | None] = mapped_column(String(255))

    # BYTEA, and always Fernet-encrypted before it gets here.
    #
    # LargeBinary rather than String is a deliberate barrier: you
    # cannot accidentally assign a plaintext str to a bytes column, so
    # the type system refuses the most likely mistake.
    credentials_enc: Mapped[bytes] = mapped_column(LargeBinary)

    # WHICH key encrypted the bytes above (Phase 5.4).
    #
    # One column, written now, never read yet. That is the point: on
    # the day the encryption key has to be rotated - because it leaked,
    # or because a year passed - the job becomes "re-encrypt every row
    # where key_version = 1, in the background, while the app keeps
    # serving both". Without it, rotation means every stored credential
    # becomes undecryptable at once and every user must reconnect.
    #
    # A column costs nothing today. Retrofitting it costs an outage.
    key_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )

    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default="{}",
    )

    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(
        back_populates="plugin_connections",
        lazy=LAZY_RAISE,
    )
