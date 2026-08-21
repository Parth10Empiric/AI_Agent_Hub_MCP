from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from api.oauth.base import (
    OAuthError,
    TokenSet,
    challenge_for,
    expiry_from,
)


"""
One class per provider.

Each one knows three things nobody else should have to: the URLs, the
exact parameter names, and the shape of the answer. Everything above
them - the state row, the CSRF check, encryption, the connection row -
is written once and never branches on the service.
"""


_TIMEOUT = 20.0


async def _post_form(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    POST a token request and return the JSON.

    `Accept: application/json` is not optional. GitHub's token endpoint
    defaults to returning a FORM-ENCODED body
    (`access_token=gho_...&scope=repo`), and json() on that raises a
    parse error that reads like a network fault.
    """

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:

        response = await client.post(
            url,
            data=data,
            headers={"Accept": "application/json"},
        )

    if response.status_code >= 400:
        # The body is not echoed to the user - it can contain the
        # client secret we just sent back at us - but the status is
        # safe and is usually enough to diagnose.
        raise OAuthError(
            f"The provider rejected the token request "
            f"(HTTP {response.status_code})."
        )

    try:
        payload = response.json()

    except ValueError as exc:
        raise OAuthError("The provider's response was not JSON.") from exc

    # OAuth's error channel is a 200 with an "error" key. Providers
    # really do this, and a client that only checks the status code
    # will happily store `access_token = None`.
    if isinstance(payload, dict) and payload.get("error"):
        raise OAuthError(
            str(
                payload.get("error_description")
                or payload.get("error")
            )
        )

    return payload


class GitHubOAuthProvider:
    """
    GitHub OAuth App.

    The simplest of the three, which is why it is worth building first:
    the token does not expire and there is no refresh token, so the
    whole lifecycle is "get it, store it, use it until the user
    revokes it".

    SCOPES: `repo` and `read:user`. NOT `admin:org`, NOT `delete_repo`.
    `repo` is already broad - it covers private repositories - but it
    is the smallest scope that lets the 18 GitHub tools work.
    """

    key = "github"
    label = "GitHub"
    scopes = ("repo", "read:user")
    uses_pkce = False

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_URL = "https://api.github.com/user"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def authorize_url(self, *, state: str, verifier: str | None) -> str:
        return f"{self.AUTHORIZE_URL}?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
            }
        )

    async def exchange(self, *, code: str, verifier: str | None) -> TokenSet:

        payload = await _post_form(
            self.TOKEN_URL,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
        )

        return _token_set(payload)

    async def refresh(self, refresh_token: str) -> TokenSet:
        # A plain OAuth App token has no expiry and no refresh token.
        # Raising is more honest than returning something that looks
        # like a refreshed token and is not.
        raise OAuthError(
            "GitHub OAuth App tokens do not expire and cannot be "
            "refreshed. If the token stopped working the user revoked "
            "it; they must reconnect."
        )

    async def identity(self, access_token: str) -> str | None:

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                self.USER_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )

        if response.status_code >= 400:
            return None

        data = response.json()

        return data.get("login") or data.get("email")


class GoogleOAuthProvider:
    """
    Google, shared by Drive and Calendar.

    One OAuth client, one consent screen, different scopes per plugin -
    which is why this class takes its scopes as an argument instead of
    hardcoding them like the others.

    THE REFRESH TOKEN TRAP

    Google returns a refresh token ONLY on the user's first consent,
    and only when `access_type=offline` is sent. Re-run the flow for an
    already-consented user and you get an access token with no refresh
    token - the connection then works for exactly one hour and dies.

    `prompt=consent` forces the consent screen every time, which forces
    a refresh token every time. It costs the user one extra click and
    removes an entire class of "it worked yesterday" bug.

    SCOPES, and why they are not `auth/drive`

    The MCP server currently asks for
    https://www.googleapis.com/auth/drive - full read/write access to
    every file in the account, including files this app never touched.
    That is a restricted scope: Google requires an annual third-party
    security assessment for it, and a client's security review will
    stop on it.

        drive.file      files this app created or the user picked
        drive.readonly  search and read
        calendar.events events, not calendar settings

    Narrower scopes mean some tools legitimately cannot work. That is
    the trade being made deliberately, and it is visible in the UI
    because the granted scopes are stored on the connection.
    """

    key = "google"
    label = "Google"
    uses_pkce = True

    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    DRIVE_SCOPES = (
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.readonly",
        "openid",
        "email",
    )

    CALENDAR_SCOPES = (
        "https://www.googleapis.com/auth/calendar.events",
        "openid",
        "email",
    )

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        key: str = "google",
        label: str = "Google",
        scopes: tuple[str, ...] = DRIVE_SCOPES,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.key = key
        self.label = label
        self.scopes = scopes

    def authorize_url(self, *, state: str, verifier: str | None) -> str:

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,

            # Without this pair there is no refresh token, and the
            # connection silently stops working after an hour.
            "access_type": "offline",
            "prompt": "consent",

            "include_granted_scopes": "true",
        }

        if verifier:
            params["code_challenge"] = challenge_for(verifier)
            params["code_challenge_method"] = "S256"

        return f"{self.AUTHORIZE_URL}?" + urlencode(params)

    async def exchange(self, *, code: str, verifier: str | None) -> TokenSet:

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }

        if verifier:
            data["code_verifier"] = verifier

        return _token_set(await _post_form(self.TOKEN_URL, data))

    async def refresh(self, refresh_token: str) -> TokenSet:

        payload = await _post_form(
            self.TOKEN_URL,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

        tokens = _token_set(payload)

        # A refresh response does NOT repeat the refresh token. Losing
        # it here would leave the connection able to refresh exactly
        # once, then expire for good.
        if tokens.refresh_token is None:
            tokens = TokenSet(
                access_token=tokens.access_token,
                refresh_token=refresh_token,
                expires_at=tokens.expires_at,
                scopes=tokens.scopes,
                raw=tokens.raw,
            )

        return tokens

    async def identity(self, access_token: str) -> str | None:

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code >= 400:
            return None

        return response.json().get("email")


class SlackOAuthProvider:
    """
    Slack (v2 OAuth).

    Two tokens come back from one exchange: a BOT token
    (`access_token`, xoxb-) and optionally a USER token
    (`authed_user.access_token`, xoxp-). The tools here act as the app,
    so the bot token is the one stored.

    SCOPES: channels:read and chat:write. NOT `admin`, which would let
    the agent reconfigure the workspace.
    """

    key = "slack"
    label = "Slack"
    scopes = ("channels:read", "chat:write", "users:read")
    uses_pkce = False

    AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
    TOKEN_URL = "https://slack.com/api/oauth.v2.access"
    TEST_URL = "https://slack.com/api/auth.test"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def authorize_url(self, *, state: str, verifier: str | None) -> str:
        return f"{self.AUTHORIZE_URL}?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                # "scope" is the BOT scope list in v2; "user_scope"
                # would request user-level tokens, which is not what
                # these tools need.
                "scope": ",".join(self.scopes),
                "state": state,
            }
        )

    async def exchange(self, *, code: str, verifier: str | None) -> TokenSet:

        payload = await _post_form(
            self.TOKEN_URL,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
        )

        # Slack answers 200 with {"ok": false, "error": "..."} - the
        # status code alone tells you nothing.
        if not payload.get("ok", True):
            raise OAuthError(str(payload.get("error", "Slack refused.")))

        return _token_set(payload)

    async def refresh(self, refresh_token: str) -> TokenSet:
        raise OAuthError(
            "This Slack bot token does not expire. If it stopped "
            "working it was revoked, and the workspace must be "
            "reconnected."
        )

    async def identity(self, access_token: str) -> str | None:

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                self.TEST_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code >= 400:
            return None

        data = response.json()

        if not data.get("ok"):
            return None

        team = data.get("team")
        user = data.get("user")

        return f"{user}@{team}" if team and user else (team or user)


def _token_set(payload: dict[str, Any]) -> TokenSet:
    """
    Normalise a token response.

    Slack nests the bot token at the top level but the user token under
    `authed_user`; everyone else uses `access_token`. The scope
    separator differs too - a space for GitHub and Google, a comma for
    Slack - so both are accepted.
    """

    access = payload.get("access_token")

    if not access and isinstance(payload.get("authed_user"), dict):
        access = payload["authed_user"].get("access_token")

    if not access:
        raise OAuthError("The provider did not return an access token.")

    raw_scope = payload.get("scope") or ""

    scopes = tuple(
        part
        for part in raw_scope.replace(",", " ").split()
        if part
    )

    return TokenSet(
        access_token=str(access),
        refresh_token=payload.get("refresh_token"),
        expires_at=expiry_from(payload.get("expires_in")),
        scopes=scopes,

        # Everything except the tokens themselves, for debugging a
        # provider that answered something unexpected. The tokens are
        # removed so this cannot become a second, unencrypted copy.
        raw={
            k: v
            for k, v in payload.items()
            if k not in {"access_token", "refresh_token", "authed_user"}
        },
    )
