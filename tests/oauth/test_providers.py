from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.oauth.base import (  # noqa: E402
    OAuthError,
    TokenSet,
    challenge_for,
    expiry_from,
    new_state,
    new_verifier,
)
from api.oauth.providers import (  # noqa: E402
    GitHubOAuthProvider,
    GoogleOAuthProvider,
    SlackOAuthProvider,
    _token_set,
)

"""
Provider-shape tests. No network, no database.

Everything here is about the parts of OAuth that are easy to get wrong
and impossible to notice: a missing access_type, a scope separator, a
refresh response that silently drops the refresh token.
"""


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


GITHUB = GitHubOAuthProvider("cid", "secret", "https://app.test/cb")
SLACK = SlackOAuthProvider("cid", "secret", "https://app.test/cb")
GOOGLE = GoogleOAuthProvider(
    "cid", "secret", "https://app.test/cb",
    key="google_drive",
    scopes=GoogleOAuthProvider.DRIVE_SCOPES,
)


# ---------------------------------------------------------------------
# Scope minimisation
# ---------------------------------------------------------------------


def test_github_does_not_request_admin_scopes():
    assert "repo" in GITHUB.scopes
    assert "read:user" in GITHUB.scopes

    for forbidden in ("admin:org", "delete_repo", "admin:repo_hook"):
        assert forbidden not in GITHUB.scopes


def test_drive_does_not_request_full_account_access():
    # The MCP server currently asks for .../auth/drive - read and write
    # to EVERY file in the account, including files this app never
    # touched. It is a restricted scope requiring an annual third-party
    # security assessment, and a client's security review stops on it.
    assert "https://www.googleapis.com/auth/drive" not in GOOGLE.scopes

    assert "https://www.googleapis.com/auth/drive.file" in GOOGLE.scopes
    assert "https://www.googleapis.com/auth/drive.readonly" in GOOGLE.scopes


def test_calendar_does_not_request_settings_access():
    scopes = GoogleOAuthProvider.CALENDAR_SCOPES

    assert "https://www.googleapis.com/auth/calendar.events" in scopes
    assert "https://www.googleapis.com/auth/calendar" not in scopes


def test_slack_does_not_request_admin():
    assert "admin" not in SLACK.scopes
    assert "chat:write" in SLACK.scopes


# ---------------------------------------------------------------------
# The authorize URL
# ---------------------------------------------------------------------


def test_github_authorize_url_carries_state_and_redirect():
    params = _params(GITHUB.authorize_url(state="abc123", verifier=None))

    assert params["state"] == "abc123"
    assert params["redirect_uri"] == "https://app.test/cb"
    assert params["client_id"] == "cid"


def test_google_asks_for_offline_access_and_forces_consent():
    """
    The trap that costs people a day.

    Google returns a refresh token ONLY on the first consent, and only
    when access_type=offline is sent. Without prompt=consent, a user who
    has connected before gets an access token with no refresh token -
    the connection then works for exactly one hour and dies, long after
    the code that caused it.
    """

    params = _params(GOOGLE.authorize_url(state="s", verifier="v" * 43))

    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"


def test_google_sends_a_pkce_challenge_not_the_verifier():
    verifier = new_verifier()

    params = _params(GOOGLE.authorize_url(state="s", verifier=verifier))

    assert params["code_challenge_method"] == "S256"

    # The VERIFIER must never leave the server - the whole point is
    # that only we can produce it at exchange time.
    assert params["code_challenge"] != verifier
    assert params["code_challenge"] == challenge_for(verifier)

    # No padding: providers reject "=" in a challenge.
    assert "=" not in params["code_challenge"]


def test_slack_separates_scopes_with_commas():
    # GitHub and Google use spaces; Slack uses commas. Getting this
    # wrong produces one scope named "channels:read chat:write", which
    # Slack rejects with an error that names neither.
    params = _params(SLACK.authorize_url(state="s", verifier=None))

    assert "," in params["scope"]
    assert " " not in params["scope"]


# ---------------------------------------------------------------------
# Reading the token response
# ---------------------------------------------------------------------


def test_token_set_reads_a_plain_response():
    tokens = _token_set(
        {
            "access_token": "gho_abc",
            "refresh_token": "r1",
            "expires_in": 3600,
            "scope": "repo read:user",
        }
    )

    assert tokens.access_token == "gho_abc"
    assert tokens.refresh_token == "r1"
    assert tokens.scopes == ("repo", "read:user")
    assert tokens.expires_at is not None


def test_token_set_reads_slacks_nested_user_token():
    tokens = _token_set(
        {"authed_user": {"access_token": "xoxp-1"}, "scope": "chat:write"}
    )

    assert tokens.access_token == "xoxp-1"


def test_token_set_refuses_a_response_with_no_token():
    try:
        _token_set({"scope": "repo"})
        raise AssertionError("a tokenless response must not be accepted")

    except OAuthError:
        pass


def test_the_raw_payload_never_carries_the_tokens():
    # `raw` is kept for debugging a provider that answered something
    # unexpected. It must not become a second, unencrypted copy of the
    # credential.
    tokens = _token_set(
        {"access_token": "secret", "refresh_token": "also-secret", "x": 1}
    )

    assert "access_token" not in tokens.raw
    assert "refresh_token" not in tokens.raw
    assert tokens.raw["x"] == 1


def test_no_expiry_means_no_expiry():
    # GitHub genuinely does not send expires_in. Treating a missing
    # value as "expires now" would make every GitHub call try to
    # refresh a token that cannot be refreshed.
    assert expiry_from(None) is None
    assert expiry_from("") is None
    assert expiry_from(0) is None
    assert expiry_from(3600) is not None


def test_the_stored_payload_keeps_the_legacy_key():
    # plugin_service.get_credential reads "credential" - the Phase 3
    # pasted-token shape. Writing both means no dual-read code path and
    # no migration for connections made before OAuth existed.
    payload = TokenSet(access_token="t", refresh_token="r").to_payload()

    assert payload["access_token"] == "t"
    assert payload["credential"] == "t"
    assert payload["refresh_token"] == "r"


# ---------------------------------------------------------------------
# State and verifier
# ---------------------------------------------------------------------


def test_state_is_long_and_unique():
    values = {new_state() for _ in range(200)}

    assert len(values) == 200
    assert all(len(v) >= 32 for v in values)


def test_verifier_is_within_the_spec_length():
    for _ in range(50):
        verifier = new_verifier()
        assert 43 <= len(verifier) <= 128
