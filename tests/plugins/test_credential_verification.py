from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.verification import (  # noqa: E402
    VERIFIERS,
    CredentialRejected,
    VerificationUnavailable,
    can_verify,
    verify_credential,
)

"""
A credential is not connected until the service says it is.

THE BUG THIS COMES FROM

    paste "hello-world-1234" into the GitHub token box
      -> 201 Created
      -> a green "Connected" badge on the plugins page
      -> every GitHub turn afterwards fails, several screens away,
         with an error nobody can trace back to that box

`connect()` encrypted the string, wrote the row and set status
"connected" without ever asking GitHub. The badge was describing our
database, not any connection.

THREE OUTCOMES, AND THE THIRD IS THE ONE PEOPLE GET WRONG

    verified      the service answered as the user's account
    rejected      the service said no -> 422, tell them to fix it
    unavailable   we could not ask -> 503, tell them to retry

Collapsing "unavailable" into "rejected" tells someone their perfectly
good token is bad, and sends them off to revoke and regenerate it.
Collapsing it into "verified" puts the original lie back, and this
time with a rationalisation attached.
"""


def client_for(handler) -> httpx.AsyncClient:
    """An httpx client that answers from `handler` instead of the network."""

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(key: str, handler, credential: str = "tok_abc12345"):
    """Run one verifier against a scripted service."""

    async def main():
        async with client_for(handler) as client:
            return await VERIFIERS[key](client, credential)

    return asyncio.run(main())


def json_response(status: int, payload: dict, headers=None):
    return httpx.Response(status, json=payload, headers=headers or {})


# ---------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------


def test_github_accepts_a_working_token():
    def handler(request):
        assert request.headers["authorization"] == "Bearer tok_abc12345"
        return json_response(
            200,
            {"login": "Parth10Empiric"},
            {"x-oauth-scopes": "repo, read:user"},
        )

    verified = run("github", handler)

    assert verified.account_label == "Parth10Empiric"
    assert verified.scopes == ("repo", "read:user")


def test_github_rejects_a_bad_token():
    def handler(request):
        return json_response(401, {"message": "Bad credentials"})

    with pytest.raises(CredentialRejected) as caught:
        run("github", handler, credential="hello-world-1234")

    # The message is for the person holding the token: which service
    # said no, and what to check.
    assert "GitHub" in str(caught.value)
    assert "hello-world-1234" not in str(caught.value)


def test_a_rate_limited_github_is_not_the_users_fault():
    # GitHub answers 403 for both "your token is wrong" and "you asked
    # too often". Reading the first as the second sends somebody to
    # regenerate a token that was fine.
    def handler(request):
        return json_response(
            403,
            {"message": "API rate limit exceeded"},
            {"x-ratelimit-remaining": "0"},
        )

    with pytest.raises(VerificationUnavailable):
        run("github", handler)


def test_a_forbidden_token_without_rate_limiting_is_rejected():
    def handler(request):
        return json_response(
            403,
            {"message": "Resource not accessible"},
            {"x-ratelimit-remaining": "4999"},
        )

    with pytest.raises(CredentialRejected):
        run("github", handler)


def test_github_outage_is_not_a_rejection():
    def handler(request):
        return json_response(500, {"message": "server error"})

    with pytest.raises(VerificationUnavailable):
        run("github", handler)


# ---------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------


def test_slack_accepts_a_working_token():
    def handler(request):
        return json_response(
            200,
            {"ok": True, "user": "parth", "team": "empiric"},
            {"x-oauth-scopes": "chat:write,channels:read"},
        )

    verified = run("slack", handler)

    assert verified.account_label == "parth@empiric"
    assert verified.scopes == ("chat:write", "channels:read")


def test_slack_says_no_with_a_200():
    # Slack answers HTTP 200 and puts the verdict in the BODY. A
    # status-code check alone passes every invalid token Slack has
    # ever issued - which is exactly the shape of bug that makes
    # "we validate the token" untrue while looking true.
    def handler(request):
        return json_response(200, {"ok": False, "error": "invalid_auth"})

    with pytest.raises(CredentialRejected) as caught:
        run("slack", handler)

    assert "invalid_auth" in str(caught.value)


def test_slack_rate_limiting_is_not_a_rejection():
    def handler(request):
        return json_response(200, {"ok": False, "error": "ratelimited"})

    with pytest.raises(VerificationUnavailable):
        run("slack", handler)


# ---------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------


def test_drive_accepts_a_token_that_can_read_drive():
    def handler(request):
        assert "drive/v3/about" in str(request.url)
        return json_response(
            200,
            {"user": {"emailAddress": "someone@example.com"}},
        )

    verified = run("google_drive", handler)

    assert verified.account_label == "someone@example.com"


def test_calendar_is_verified_against_calendar():
    # A Google token scoped for Drive is valid, and cannot read a
    # calendar. Verifying every Google plugin against one generic
    # endpoint would accept it as a Calendar connection and fail later.
    def handler(request):
        assert "calendar/v3" in str(request.url)
        return json_response(
            200,
            {"items": [{"id": "someone@example.com", "primary": True}]},
        )

    verified = run("google_calendar", handler)

    assert verified.account_label == "someone@example.com"


def test_google_rejects_a_token_without_that_access():
    def handler(request):
        return json_response(401, {"error": "invalid_credentials"})

    with pytest.raises(CredentialRejected):
        run("google_drive", handler)


# ---------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------


def test_every_connectable_service_can_be_verified():
    # The registry's namespaces and the verifier table have to stay in
    # step. A plugin with no verifier cannot be connected by token at
    # all - which is deliberate, and is worth failing a test over
    # rather than discovering in production.
    for key in ("github", "slack", "google_drive", "google_calendar"):
        assert can_verify(key)


def test_an_unverifiable_service_is_refused_not_trusted():
    # The strict choice. "Store it and hope" is the behaviour being
    # fixed, and it would creep straight back in through the first
    # plugin somebody adds without a verifier.
    with pytest.raises(VerificationUnavailable):
        asyncio.run(verify_credential("brand_new_service", "tok_abc12345"))


def test_a_network_failure_is_never_read_as_a_bad_token(monkeypatch):
    async def explode(client, credential):
        raise httpx.ConnectError("dns is having a day")

    monkeypatch.setitem(VERIFIERS, "github", explode)

    with pytest.raises(VerificationUnavailable):
        asyncio.run(verify_credential("github", "tok_abc12345"))


def test_an_unexpected_shape_fails_closed(monkeypatch):
    # A field that moved, malformed JSON, anything unforeseen. The one
    # outcome that must never follow from a surprise is "connected".
    async def explode(client, credential):
        raise KeyError("the API changed under us")

    monkeypatch.setitem(VERIFIERS, "github", explode)

    with pytest.raises(VerificationUnavailable):
        asyncio.run(verify_credential("github", "tok_abc12345"))
