from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable


"""
What every OAuth provider has in common - and what it does not.

The temptation with OAuth is to write one helper function, because the
RFC describes one flow. Three providers in, that function is a nest of
`if provider == "google"`. They genuinely differ:

    GitHub    tokens do not expire; there is no refresh token at all
    Google    refresh token is returned ONLY on the first consent,
              and only if you ask for it explicitly
    Slack     the useful token is nested under a different key, and
              bot tokens do not expire unless rotation is enabled

So the shape here is an interface with one small class per provider.
Everything above it - the state row, the CSRF check, the encryption,
the connection row - is written once and never branches on the service.
"""


class OAuthError(Exception):
    """The provider refused, or answered with something unusable."""


class ProviderNotConfigured(OAuthError):
    """
    No client id / secret for this service.

    A separate exception because it is not a failure of the flow - it
    is a deployment that has not been finished. The API answers 503 and
    says which setting is missing, rather than bouncing the user to a
    consent screen that cannot work.
    """


@dataclass(frozen=True, slots=True)
class TokenSet:
    """
    What a provider hands back, normalised.

    `expires_at` is computed here, from `expires_in`, at the moment of
    exchange. Providers send a DURATION; a duration is only meaningful
    relative to when it was issued, and storing the duration means
    every later reader has to know when that was.
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """
        The dict that gets encrypted into credentials_enc.

        A dict, not a bare string, which is why moving from "a pasted
        personal access token" to "an OAuth token set" needs no
        migration: the column is opaque bytes either way.
        """

        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at else None
            ),
            "scopes": list(self.scopes),

            # Kept so a token stored by an older build is still
            # readable by plugin_service.get_credential, which looks
            # for "credential". One key, and no dual-read code path
            # anywhere else.
            "credential": self.access_token,
        }


def expiry_from(expires_in: Any) -> datetime | None:
    """
    Turn a provider's `expires_in` (seconds) into an absolute time.

    Returns None for a missing or unparseable value, which is the
    correct reading: GitHub genuinely does not send one, and a token
    with no expiry must not be treated as expiring now.
    """

    try:
        seconds = int(expires_in)

    except (TypeError, ValueError):
        return None

    if seconds <= 0:
        return None

    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def new_state() -> str:
    """
    A fresh, unguessable `state`.

    token_urlsafe, not uuid4: a UUID is 122 bits of randomness wrapped
    in a format people expect to be able to parse, and some libraries
    happily accept a malformed one. This is 256 bits of opaque text
    that nothing will try to interpret.
    """

    return secrets.token_urlsafe(32)


def new_verifier() -> str:
    """The PKCE `code_verifier`: 43-128 characters, unreserved."""

    return secrets.token_urlsafe(64)[:128]


def challenge_for(verifier: str) -> str:
    """
    The S256 `code_challenge` for a verifier.

    Base64URL of the SHA-256, with padding stripped - the spec is
    explicit that the "=" must not be sent, and providers reject it.
    """

    digest = hashlib.sha256(verifier.encode("ascii")).digest()

    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@runtime_checkable
class OAuthProvider(Protocol):
    """One service's half of the flow."""

    key: str
    label: str
    scopes: tuple[str, ...]
    uses_pkce: bool

    def authorize_url(self, *, state: str, verifier: str | None) -> str:
        ...

    async def exchange(
        self,
        *,
        code: str,
        verifier: str | None,
    ) -> TokenSet:
        ...

    async def refresh(self, refresh_token: str) -> TokenSet:
        ...

    async def identity(self, access_token: str) -> str | None:
        ...
