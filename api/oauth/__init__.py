"""OAuth flows, one small class per provider. See base.py."""

from api.oauth.base import (
    OAuthError,
    OAuthProvider,
    ProviderNotConfigured,
    TokenSet,
    challenge_for,
    new_state,
    new_verifier,
)
from api.oauth.registry import (
    build_provider,
    redirect_uri_for,
    supports_oauth,
)

__all__ = [
    "OAuthError",
    "OAuthProvider",
    "ProviderNotConfigured",
    "TokenSet",
    "build_provider",
    "challenge_for",
    "new_state",
    "new_verifier",
    "redirect_uri_for",
    "supports_oauth",
]
