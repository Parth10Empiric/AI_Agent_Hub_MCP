from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, UUIDMixin


class OAuthState(UUIDMixin, Base):
    """
    One in-flight authorisation.

    THIS ROW IS THE SECURITY OF THE WHOLE FLOW.

    It does three jobs, and every one of them is load-bearing:

      1. CSRF. The `state` we sent must come back. Without that check,
         anyone can forge a callback to your endpoint carrying THEIR
         authorization code, and your user's account silently gets
         connected to the attacker's GitHub.

      2. IDENTITY. The provider redirects a BROWSER to the callback. A
         browser following a 302 sends cookies - it does not send
         `Authorization: Bearer ...`, and this API is bearer
         authenticated. So the callback cannot know who the user is
         from the request. It knows because this row says so.

      3. SINGLE USE. `used_at` makes a replayed callback fail the
         second time. This is why the state is a ROW and not a signed
         token: a JWT can be made tamper-proof and short-lived, but
         "has this been used?" is a fact about the world, not a fact
         you can sign into a string.
    """

    __tablename__ = "oauth_states"

    __table_args__ = (
        # The expiry sweep. Rows are tiny and short-lived, but nothing
        # deletes them on the unhappy path - a user who opens the
        # consent screen and closes the tab leaves one behind forever.
        Index("ix_oauth_states_expiry", "expires_at"),
    )

    # The opaque value that travels to the provider and back.
    #
    # Unique and indexed because looking it up by value is the only
    # read this table serves, and two rows sharing a state would mean
    # a callback could be attributed to the wrong user.
    state: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    # "github", "google_drive", "slack". Checked against the state on
    # the way back, so a state minted for Slack cannot be redeemed on
    # the GitHub callback.
    plugin_key: Mapped[str] = mapped_column(String(64))

    # PKCE. The random secret whose SHA-256 was sent to the provider as
    # `code_challenge`; the provider will only exchange the code if we
    # can produce the original.
    #
    # Not strictly required for a confidential client (we hold a client
    # secret), but it costs ten lines and it closes the authorization
    # code interception attack completely - including the case where
    # the code leaks through a referrer header or a proxy log.
    code_verifier: Mapped[str | None] = mapped_column(String(128))

    # Where to send the browser when this is finished. Validated
    # against our own frontend base before use - an open redirect here
    # would be a phishing primitive handed out for free.
    redirect_to: Mapped[str | None] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL means "never redeemed". Set - not deleted - on use, so a
    # replay is recognised rather than merely not found. A deleted row
    # and a forged state look identical; a used row does not.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_usable(self) -> bool:
        return self.used_at is None
