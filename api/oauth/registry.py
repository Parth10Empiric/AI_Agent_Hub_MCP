from __future__ import annotations

from api.oauth.base import OAuthProvider, ProviderNotConfigured
from api.oauth.providers import (
    GitHubOAuthProvider,
    GoogleOAuthProvider,
    SlackOAuthProvider,
)
from api.settings import APISettings


"""
Which provider serves which plugin key.

The keys are the Phase 2 NAMESPACES - "github", "google_drive",
"google_calendar", "slack" - so a plugin_connections row, a
ToolDefinition and an OAuth provider all agree on the same identifier
with nothing translating between them.

Drive and Calendar map to the SAME provider class with different
scopes. They share a Google client, a consent screen and a token
endpoint; only the scope list differs, and that is an argument.
"""


# One callback path per plugin. Registered with the provider, and
# constant - never assembled from anything in the request.
CALLBACK_PATH = "/api/plugins/{key}/oauth/callback"


def redirect_uri_for(settings: APISettings, key: str) -> str:
    base = settings.oauth_redirect_base.rstrip("/")

    return base + CALLBACK_PATH.format(key=key)


def _google(
    settings: APISettings,
    key: str,
    label: str,
    scopes: tuple[str, ...],
) -> OAuthProvider:

    return GoogleOAuthProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        redirect_uri=redirect_uri_for(settings, key),
        key=key,
        label=label,
        scopes=scopes,
    )


def build_provider(settings: APISettings, key: str) -> OAuthProvider:
    """
    The provider for one plugin key.

    Built per call rather than cached, because it carries the redirect
    URI and the secret - both configuration, both cheap to assemble,
    and neither worth keeping alive in a module-level global where a
    test cannot replace it.
    """

    if key == "github":
        _require(settings.github_client_id, "GITHUB_CLIENT_ID")

        return GitHubOAuthProvider(
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret.get_secret_value(),
            redirect_uri=redirect_uri_for(settings, key),
        )

    if key == "google_drive":
        _require(settings.google_client_id, "GOOGLE_CLIENT_ID")

        return _google(
            settings,
            key,
            "Google Drive",
            GoogleOAuthProvider.DRIVE_SCOPES,
        )

    if key == "google_calendar":
        _require(settings.google_client_id, "GOOGLE_CLIENT_ID")

        return _google(
            settings,
            key,
            "Google Calendar",
            GoogleOAuthProvider.CALENDAR_SCOPES,
        )

    if key == "slack":
        _require(settings.slack_client_id, "SLACK_CLIENT_ID")

        return SlackOAuthProvider(
            client_id=settings.slack_client_id,
            client_secret=settings.slack_client_secret.get_secret_value(),
            redirect_uri=redirect_uri_for(settings, key),
        )

    raise ProviderNotConfigured(
        f"{key!r} has no OAuth provider. Connect it with a token instead."
    )


def _require(value: str, name: str) -> None:
    if not value:
        raise ProviderNotConfigured(
            f"{name} is not set. Add it to .env to enable this flow."
        )


def supports_oauth(settings: APISettings, key: str) -> bool:
    """
    Can this service be connected with OAuth right now?

    Answered from configuration, not from a hardcoded list, so the UI
    shows "Connect with GitHub" only where it would actually work and
    falls back to the paste-a-token dialog everywhere else. A button
    that leads to a broken consent screen is worse than no button.
    """

    try:
        build_provider(settings, key)
        return True

    except ProviderNotConfigured:
        return False
