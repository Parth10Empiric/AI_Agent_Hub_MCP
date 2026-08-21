from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from api.middleware import RateLimitMiddleware  # noqa: E402
from api.ratelimit import InMemoryRateLimiter  # noqa: E402

"""
The HTTP layer of the limit (Phase 5.7).

A real ASGI app with the real middleware - no mocks - because the parts
worth testing here are the ones a mock would paper over: which paths
are exempt, what headers come back, and whether an anonymous caller
gets their own bucket or shares one with the world.
"""


class Settings:
    rate_limit_enabled = True
    api_requests_per_minute = 3
    jwt_secret_key = None


def _app(settings=None) -> tuple[TestClient, InMemoryRateLimiter]:

    limiter = InMemoryRateLimiter()

    app = FastAPI()

    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        settings=settings or Settings(),
    )

    @app.get("/api/agents")
    def agents():
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/plugins/github/oauth/callback")
    def callback():
        return {"ok": True}

    return TestClient(app), limiter


def test_requests_are_limited_and_answer_429():
    client, _ = _app()

    codes = [client.get("/api/agents").status_code for _ in range(5)]

    assert codes == [200, 200, 200, 429, 429]


def test_a_refusal_carries_retry_after():
    client, _ = _app()

    for _ in range(3):
        client.get("/api/agents")

    response = client.get("/api/agents")

    # Without this a client either gives up or hammers you - and
    # hammering is the behaviour the limit exists to stop.
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert response.headers["x-ratelimit-limit"] == "3"
    assert response.headers["x-ratelimit-remaining"] == "0"


def test_every_response_carries_the_remaining_budget():
    client, _ = _app()

    first = client.get("/api/agents")

    # On SUCCESS too, so a well-behaved client can slow down before it
    # hits the wall rather than discovering the wall.
    assert first.headers["x-ratelimit-remaining"] == "2"


def test_health_is_never_limited():
    client, _ = _app()

    # Your monitor is not an attacker, and a limited health check
    # reports the outage it caused.
    codes = [client.get("/api/health").status_code for _ in range(20)]

    assert set(codes) == {200}


def test_the_oauth_callback_is_never_limited():
    client, _ = _app()

    # A user coming back from a consent screen gets exactly ONE chance.
    # A 429 here loses the authorization code and the flow restarts
    # from a page they have already left.
    codes = [
        client.get("/api/plugins/github/oauth/callback").status_code
        for _ in range(20)
    ]

    assert set(codes) == {200}


def test_the_limiter_can_be_switched_off():
    class Off(Settings):
        rate_limit_enabled = False

    client, _ = _app(Off())

    codes = [client.get("/api/agents").status_code for _ in range(20)]

    assert set(codes) == {200}


def test_anonymous_callers_are_counted_by_address_not_pooled():
    """
    Every unauthenticated request sharing one bucket called "anonymous"
    would let a single loop lock out every visitor - including the
    login page, which is the one endpoint a locked-out user needs.
    """

    client, limiter = _app()

    client.get("/api/agents")

    assert any(k.startswith("http:ip:") for k in limiter._windows)
    assert not any("anonymous" in k for k in limiter._windows)


# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------


def test_login_is_limited_on_both_ip_and_account():
    """
    Neither key alone is enough.

        per IP       stops one machine grinding a password list
                     against many accounts

        per ACCOUNT  stops a distributed attempt on ONE account, where
                     every request comes from a different address

    And behind a proxy every user shares one IP - so the account key is
    not a nicety, it is what stops one attacker locking out everybody.
    """

    from fastapi import HTTPException

    from api.routers.auth import _enforce_login_limit

    class LoginSettings:
        login_attempts = 3
        login_window_seconds = 900

    limiter = InMemoryRateLimiter()
    settings = LoginSettings()

    # Same address, three DIFFERENT accounts: the IP budget runs out
    # even though no single account has been touched twice.
    for i in range(3):
        _enforce_login_limit(limiter, settings, "1.2.3.4", f"u{i}@x.test")

    try:
        _enforce_login_limit(limiter, settings, "1.2.3.4", "u4@x.test")
        raise AssertionError("a fourth attempt from one address was allowed")

    except HTTPException as exc:
        assert exc.status_code == 429
        assert "Retry-After" in exc.headers


def test_login_counts_one_account_across_many_addresses():
    from fastapi import HTTPException

    from api.routers.auth import _enforce_login_limit

    class LoginSettings:
        login_attempts = 3
        login_window_seconds = 900

    limiter = InMemoryRateLimiter()
    settings = LoginSettings()

    for i in range(3):
        _enforce_login_limit(limiter, settings, f"10.0.0.{i}", "victim@x.test")

    try:
        _enforce_login_limit(limiter, settings, "10.0.0.99", "victim@x.test")
        raise AssertionError("a distributed attempt on one account continued")

    except HTTPException as exc:
        assert exc.status_code == 429


def test_login_keys_are_case_insensitive_on_the_email():
    from fastapi import HTTPException

    from api.routers.auth import _enforce_login_limit

    class LoginSettings:
        login_attempts = 2
        login_window_seconds = 900

    limiter = InMemoryRateLimiter()
    settings = LoginSettings()

    # Login@x.test and login@x.test are ONE account and must share one
    # budget - otherwise the limit is bypassed by pressing shift.
    _enforce_login_limit(limiter, settings, "1.1.1.1", "Login@x.test")
    _enforce_login_limit(limiter, settings, "1.1.1.2", "login@x.test")

    try:
        _enforce_login_limit(limiter, settings, "1.1.1.3", "LOGIN@X.TEST")
        raise AssertionError("case changed the bucket")

    except HTTPException:
        pass
