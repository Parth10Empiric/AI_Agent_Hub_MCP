from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, UUIDMixin


class RefreshToken(UUIDMixin, Base):
    """
    One issued refresh token.

    The raw token is NEVER stored. Only its SHA-256 hash lives here, so
    a leaked backup or a read-only SQL injection yields nothing usable.

    ROTATION

    Every use of a refresh token consumes it and issues a new one:

        login     -> token A
        /refresh  -> A.used_at set, token B issued
        /refresh  -> B.used_at set, token C issued

    REUSE DETECTION

    If A is presented again after being consumed, something is wrong.
    Either the real client replayed it or an attacker stole it, and
    there is no way to tell which - so the safe assumption is theft:

        a used token is presented again
              |
              v
        revoke the entire FAMILY
              |
              v
        attacker and user are both logged out;
        the user can log in again, the attacker cannot

    That is what family_id exists for. Without it you could only revoke
    the one stolen token, and the attacker would keep the newer one
    they had already rotated into.
    """

    __tablename__ = "refresh_tokens"

    __table_args__ = (
        # Revoking a family is the hot path during an incident, and
        # cleaning up expired rows is a routine job. Both filter on
        # exactly these columns.
        Index("ix_refresh_tokens_family", "family_id", "expires_at"),
    )

    # SHA-256 hex is always 64 characters.
    #
    # Unique because a collision would mean two sessions sharing an
    # identity, and indexed because looking a token up by hash is the
    # only read this table ever serves.
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    # Shared by every token in one rotation chain. A fresh login starts
    # a new family; a refresh inherits the current one.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL means still valid.
    #
    # The token is not deleted when consumed - it is marked. A deleted
    # row cannot be recognised as stolen when it comes back, which
    # would silently disable reuse detection.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Set by logout, or by reuse detection revoking the whole family.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    # Context for a future "your active sessions" screen, and for
    # telling a user where a suspicious login came from.
    user_agent: Mapped[str | None] = mapped_column(String(255))

    # 45 characters is the longest possible IPv6 address, including an
    # IPv4-mapped suffix.
    ip_address: Mapped[str | None] = mapped_column(String(45))

    @property
    def is_usable(self) -> bool:
        """
        A token is usable only if it has never been used and never
        revoked. Expiry is checked separately, against the database
        clock rather than this process.
        """

        return self.used_at is None and self.revoked_at is None
