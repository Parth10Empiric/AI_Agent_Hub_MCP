from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ToolSummary(BaseModel):
    """
    One tool, as the API describes it.

    Every field here is produced by Phase 2 classification. Nothing is
    hand-maintained, so a new tool arrives fully described: what it
    does, how dangerous it is, and whether it will ask before running.
    """

    name: str
    title: str | None
    description: str | None

    # read | write | delete | admin
    operation: str

    # safe | low | medium | high | critical
    risk_level: str

    read_only: bool
    requires_approval: bool

    # Two strings per tool: specific and wildcard, e.g.
    # "github:issue:write" and "github:*:write". Phase 5 grants
    # against these.
    permissions: list[str]


class PluginSummary(BaseModel):
    """A service, as it appears in the catalogue list."""

    key: str
    label: str
    description: str
    icon: str
    category: str
    auth_type: str
    docs_url: str | None

    tool_count: int

    # Counts by operation, so the UI can say "18 tools: 12 read,
    # 4 write, 2 admin" without fetching every tool.
    operations: dict[str, int]

    connected: bool
    account_label: str | None = None
    status: str | None = None

    # Can this service be connected with a real OAuth flow right now?
    #
    # Answered from CONFIGURATION - whether a client id and secret are
    # set - not from a hardcoded list. So the UI shows "Connect with
    # GitHub" only where that button would actually work, and falls
    # back to the paste-a-token dialog everywhere else. A button that
    # leads to a broken consent screen is worse than no button.
    oauth_available: bool = False

    # What the flow will ask the provider for. Shown before the user
    # leaves the app, because "we are about to request access to your
    # files" is a sentence they should read on OUR page, where they
    # trust the context, rather than on a consent screen they are
    # trained to click through.
    oauth_scopes: list[str] = Field(default_factory=list)


class PluginDetail(PluginSummary):
    """A service, plus its full tool list."""

    tools: list[ToolSummary]


class ConnectRequest(BaseModel):
    """
    Connect a service by pasting a credential.

    Phase 5 replaces this with real OAuth. The table does not change -
    only what fills credentials_enc.
    """

    # The maximum matters: this value is encrypted and stored, and an
    # unbounded field on an authenticated endpoint is still a way to
    # fill a database.
    credential: str = Field(min_length=8, max_length=4096)

    # Shown in the UI so a user can tell two accounts apart. Never the
    # credential itself.
    account_label: str | None = Field(default=None, max_length=255)

    scopes: list[str] = Field(default_factory=list)


class ConnectionCheck(BaseModel):
    """
    The result of re-testing a stored credential.

    A RESULT, not an error. "The check ran and the service said no" is
    a successful request whose answer happens to be negative - and it
    has to be, because recording that answer WRITES (status becomes
    "revoked") and an endpoint that raises gets its transaction rolled
    back by api/db/session.get_db. Raising would report the bad token
    and then forget it.
    """

    # Did the service accept the credential?
    valid: bool

    # A sentence for the user: which service said what, and what to do.
    # Never contains the credential.
    detail: str

    # The connection as it stands AFTER the check, so the client can
    # render the new status without a second request.
    connection: "ConnectionRead"


class ConnectionRead(BaseModel):
    """
    A user's connection to one service.

    An ALLOW-LIST, exactly like UserRead. credentials_enc is not here
    and must never be: returning it - even encrypted - hands an
    attacker the ciphertext to work on offline, and costs the user
    nothing to have withheld.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plugin_key: str
    status: str
    account_label: str | None
    scopes: list[str]
    connected_at: datetime | None
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class OAuthStart(BaseModel):
    """
    Where to send the browser to begin an OAuth flow.

    A URL IN A JSON BODY, not a 302.

    This endpoint is bearer-authenticated, and a browser NAVIGATION
    cannot carry an Authorization header. If it answered with a
    redirect the frontend would have to navigate here directly - and
    then it could not authenticate at all.

    So the frontend fetches this with a normal authenticated request
    and then sets window.location to `authorize_url`. One extra round
    trip, and no second authentication mechanism to maintain.
    """

    authorize_url: str

    # Echoed back so the UI can show "you are about to grant: read your
    # files, send messages" BEFORE the user leaves the app - on a page
    # where they trust the context, rather than on a consent screen
    # they have been trained to click through.
    scopes: list[str] = Field(default_factory=list)
