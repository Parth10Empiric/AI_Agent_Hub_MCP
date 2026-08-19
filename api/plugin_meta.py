from __future__ import annotations

from dataclasses import dataclass


# Presentation metadata for services. DATA, not logic.
#
# THIS FILE IS OPTIONAL, AND THAT IS THE POINT.
#
# The plugin catalogue is derived from the LIVE registry - whatever the
# MCP server exposes at startup. This file only adds human-facing
# polish on top: a nicer label, an icon, a description.
#
# Add a new service to the MCP server and it appears in the API
# immediately, with its tools, risk levels and permission scopes
# already correct, using a display name derived from its namespace. If
# that derived name is good enough, nothing here needs to change. Add
# an entry only when you want a better icon or a real description.
#
# The alternative - a `plugins` table, or a hardcoded list of the four
# services - would be a SECOND source of truth. It would drift the
# moment someone added a service, and every new integration would need
# a backend change and a migration.


@dataclass(frozen=True, slots=True)
class PluginPresentation:
    label: str
    description: str
    icon: str
    category: str
    auth_type: str
    docs_url: str | None = None


# Words that must not be title-cased naively when a name is derived.
_ACRONYMS = {
    "api": "API",
    "aws": "AWS",
    "db": "DB",
    "gcp": "GCP",
    "github": "GitHub",
    "gitlab": "GitLab",
    "http": "HTTP",
    "sql": "SQL",
}


PRESENTATION: dict[str, PluginPresentation] = {
    "github": PluginPresentation(
        label="GitHub",
        description=(
            "Repositories, issues, pull requests, commits and file "
            "contents."
        ),
        icon="github",
        category="development",
        auth_type="personal_access_token",
        docs_url="https://github.com/settings/tokens",
    ),
    "google_drive": PluginPresentation(
        label="Google Drive",
        description="Search, read, create and share files and folders.",
        icon="drive",
        category="storage",
        auth_type="oauth2",
        docs_url="https://console.cloud.google.com/apis/credentials",
    ),
    "google_calendar": PluginPresentation(
        label="Google Calendar",
        description="Events, availability and calendar management.",
        icon="calendar",
        category="productivity",
        auth_type="oauth2",
        docs_url="https://console.cloud.google.com/apis/credentials",
    ),
    "slack": PluginPresentation(
        label="Slack",
        description="Channels, messages, threads, users and files.",
        icon="slack",
        category="communication",
        auth_type="bot_token",
        docs_url="https://api.slack.com/apps",
    ),
}


def derive_label(namespace: str) -> str:
    """
    Turn a namespace into a readable name with no configuration.

        google_calendar  ->  Google Calendar
        github           ->  GitHub
        notion           ->  Notion
        my_crm_api       ->  My CRM API

    This is what makes an unknown service usable the moment it appears,
    instead of showing a raw identifier until someone remembers to add
    it to a table.
    """

    return " ".join(
        _ACRONYMS.get(part.lower(), part.capitalize())
        for part in namespace.split("_")
        if part
    )


def presentation_for(namespace: str) -> PluginPresentation:
    """
    Look up presentation metadata, generating a sensible default for
    any service this file has never heard of.

    Never raises and never returns None: an unknown service must be
    usable, not invisible.
    """

    known = PRESENTATION.get(namespace)

    if known is not None:
        return known

    label = derive_label(namespace)

    return PluginPresentation(
        label=label,
        description=f"Tools provided by the {label} service.",
        icon="plugin",
        category="other",

        # The safe default. A service whose auth style is unknown is
        # offered the generic "paste a token" flow, which works for
        # most things and cannot silently skip authentication.
        auth_type="token",
    )
