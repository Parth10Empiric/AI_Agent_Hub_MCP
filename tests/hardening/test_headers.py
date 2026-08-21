from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, HTTPException  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from api.errors import install_error_handlers  # noqa: E402
from api.middleware import (  # noqa: E402
    ErrorEnvelopeMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

"""
Transport hardening (Phase 5.9).

Everything here defends against the BROWSER being wrong about your
intent - sniffing a content type, leaking a URL in a Referer, framing
the approval dialog. None of it happens on the server, so none of it
can be fixed by server logic. All you can do is say so in a header.
"""


class Settings:
    def __init__(self, production: bool = False) -> None:
        self.is_production = production


def _app(production: bool = False) -> TestClient:

    app = FastAPI()

    # Same order as api/main.py: innermost first.
    app.add_middleware(ErrorEnvelopeMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=Settings(production))
    app.add_middleware(RequestContextMiddleware)

    install_error_handlers(app)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("the database is on fire and the password is hunter2")

    @app.get("/refused")
    def refused():
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts.",
            headers={"Retry-After": "42"},
        )

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------


def test_nosniff_is_always_set():
    # Stops a browser guessing that JSON is HTML and running it - which
    # is why "upload a file called x.json containing <script>" is not
    # an attack.
    response = _app().get("/ok")

    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_referrer_policy_does_not_leak_urls():
    # These URLs carry agent ids and conversation ids.
    response = _app().get("/ok")

    assert (
        response.headers["referrer-policy"]
        == "strict-origin-when-cross-origin"
    )


def test_framing_is_denied():
    # The clickjacking defence, and it matters more here than in most
    # apps: an invisible frame over a page the user thinks is something
    # else, with Approve exactly where they are about to click.
    response = _app().get("/ok")

    assert response.headers["x-frame-options"] == "DENY"


def test_hsts_is_production_only():
    """
    A browser that has seen HSTS refuses http:// for the whole
    max-age, and there is no way to take it back.

    Sent from a dev server it breaks local development for a year, on
    every developer's machine, with no obvious cause.
    """

    assert "strict-transport-security" not in _app(False).get("/ok").headers

    production = _app(True).get("/ok")

    assert "max-age=31536000" in production.headers["strict-transport-security"]


def test_headers_are_on_error_responses_too():
    # An error page is still a page a browser will interpret.
    response = _app().get("/boom")

    assert response.status_code == 500
    assert response.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------
# The error envelope
# ---------------------------------------------------------------------


def test_an_unhandled_error_never_leaks_its_cause():
    """
    The message is deliberately vague. A stack trace or a raw database
    error in a browser payload is an information leak - and this one
    would have handed over a password.
    """

    response = _app().get("/boom")

    body = response.text

    assert "hunter2" not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
    assert "/boom" not in body


def test_every_error_carries_the_shape_the_frontend_reads():
    # lib/api/client.ts has read body.error.code and
    # body.error.request_id since Phase 4, and nothing produced them.
    body = _app().get("/boom").json()

    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"].startswith("req_")
    assert body["error"]["message"]


def test_a_deliberate_refusal_keeps_its_message_and_headers():
    response = _app().get("/refused")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"

    # Losing this would turn a polite client into a hot loop.
    assert response.headers["retry-after"] == "42"

    # These messages were written to be read by a user.
    assert "Too many sign-in attempts" in response.json()["error"]["message"]


def test_the_request_id_is_returned_and_matches_the_body():
    response = _app().get("/boom")

    assert response.headers["x-request-id"] == (
        response.json()["error"]["request_id"]
    )


def test_a_forged_request_id_header_is_not_echoed_blindly():
    # Echoing arbitrary client input into a log column is how a header
    # becomes a log-injection vector.
    response = _app().get(
        "/ok", headers={"X-Request-ID": "evil\ninjected: line"}
    )

    assert "\n" not in response.headers["x-request-id"]
    assert response.headers["x-request-id"].startswith("req_")


def test_a_sane_request_id_from_a_caller_is_honoured():
    # A gateway that already assigned one should not have it replaced,
    # or the two systems' logs cannot be joined.
    response = _app().get("/ok", headers={"X-Request-ID": "req_abc123"})

    assert response.headers["x-request-id"] == "req_abc123"
