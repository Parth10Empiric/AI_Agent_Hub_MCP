from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.logging import get_logger

from api.ratelimit import RateLimiter
from api.request_context import (
    new_request_id,
    reset_request_context,
    set_request_context,
)
from api.security import TokenError, decode_access_token
from api.settings import APISettings


logger = get_logger(__name__)


"""
The per-request limit (Phase 5.7).

This is the COST limit at its coarsest: how many HTTP requests one
user may make per minute. It catches a client stuck in a loop, a
misbehaving script, and a dashboard that refetches on every render.

WHAT IT DELIBERATELY DOES NOT CATCH

Anything measured in something other than requests. One POST to
/messages/stream is ONE request, one agent turn, and possibly nine
tool calls - and it can stay open for five minutes while an approval
waits. Turns, tool calls and writes are limited where they actually
happen, not here.

WHY IT IDENTIFIES THE USER ITSELF

The obvious design is a FastAPI dependency, which gets `CurrentUser`
for free. But a dependency runs per ENDPOINT, so every endpoint has to
remember to ask for it - and the one that forgets is unlimited. A
middleware wraps everything by construction, including endpoints
written next year.

The cost is decoding the token twice: once here, once in
get_current_user. That is a signature check on a short string, not a
database call, and buying "no endpoint can forget" with it is a good
trade.
"""


# Never limited, and each for its own reason.
EXEMPT_PATHS = frozenset(
    {
        # Your monitor is not an attacker, and a limited health check
        # reports the outage it caused.
        "/api/health",
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
)

# A user coming back from a consent screen gets exactly one chance. A
# 429 here loses the authorization code, and the flow has to start
# over from a page they have already left.
EXEMPT_PREFIXES = ("/api/plugins/",)

EXEMPT_SUFFIXES = ("/oauth/callback",)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user request limiting, applied to everything."""

    def __init__(self, app, limiter: RateLimiter, settings: APISettings):
        super().__init__(app)

        self._limiter = limiter
        self._settings = settings

    async def dispatch(self, request: Request, call_next):

        if not self._settings.rate_limit_enabled or _exempt(request):
            return await call_next(request)

        subject = self._subject(request)

        decision = self._limiter.check(
            f"http:{subject}",
            self._settings.api_requests_per_minute,
            60,
        )

        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "You are sending requests faster than this "
                        "service allows. Wait a moment and try again."
                    ),
                },
                headers=_headers(decision),
            )

        response = await call_next(request)

        # On EVERY response, not just refusals. A client that can see
        # its budget shrinking can slow down before it hits the wall;
        # one that only learns at zero cannot.
        for name, value in _headers(decision).items():
            response.headers[name] = value

        return response

    def _subject(self, request: Request) -> str:
        """
        Who to count against: the user if we can tell, else the IP.

        Falling back to the IP matters. Without it every
        unauthenticated request shares one bucket called "anonymous",
        and one loop would lock out every visitor including the login
        page.
        """

        header = request.headers.get("authorization", "")

        if header.lower().startswith("bearer "):

            try:
                user_id = decode_access_token(
                    header[7:].strip(),
                    self._settings,
                )
                return f"user:{user_id}"

            except TokenError:
                # An invalid token is not a user. Counted by address,
                # which is also the right bucket for someone probing
                # with forged tokens.
                pass

        return f"ip:{client_ip(request)}"


def client_ip(request: Request) -> str:
    """
    The caller's address, as well as it can be known.

    request.client.host is the CONNECTING socket. Behind nginx or
    Vercel that is the proxy, so every user shares one address - and a
    limit keyed on it would lock out everybody at once.

    X-Forwarded-For fixes that and introduces a worse problem: anyone
    can send that header. The rule is to trust it only from a hop you
    control, and to read the RIGHTMOST entry your proxy appended, never
    the leftmost - the leftmost is whatever the client claimed.

    Configuring uvicorn with --proxy-headers --forwarded-allow-ips is
    the proper fix; this is the safe reading until then.
    """

    return request.client.host if request.client else "unknown"


def _exempt(request: Request) -> bool:

    path = request.url.path

    if path in EXEMPT_PATHS:
        return True

    if any(path.endswith(suffix) for suffix in EXEMPT_SUFFIXES):
        return True

    return False


def _headers(decision) -> dict[str, str]:
    """
    The standard headers, so a client can behave without guessing.

    Retry-After is the one that matters: a refusal with no "when"
    turns a polite client into a hot loop, which is the behaviour the
    limit exists to stop.
    """

    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }

    if not decision.allowed:
        headers["Retry-After"] = str(decision.retry_after)

    return headers


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Give every request an id, and put it where the audit writer can
    reach it (Phase 5.8).

    WHY THIS IS WORTH A MIDDLEWARE OF ITS OWN

    One id ties together the HTTP response, every log line, and every
    audit row from one request. A user reporting "it failed at 2:14 and
    said req_8f3a" hands you their entire request in a single query,
    instead of a timestamp to guess from.

    OUTERMOST, so the id exists before anything else runs - including
    the rate limiter, whose refusals are worth being able to trace back
    to the client that caused them.
    """

    async def dispatch(self, request: Request, call_next):

        request_id = (
            # Honour an id the caller supplied ONLY if it looks like
            # one of ours. Echoing arbitrary client input into a log
            # column is how a header becomes a log-injection vector.
            _valid_request_id(request.headers.get("x-request-id"))
            or new_request_id()
        )

        tokens = set_request_context(request_id, client_ip(request))

        try:
            response = await call_next(request)

        finally:
            # ALWAYS. Without this a request that shares a context with
            # the next one leaks its id into that one's audit rows - a
            # quiet, permanent lie in the table kept because it does
            # not lie.
            reset_request_context(tokens)

        response.headers["X-Request-ID"] = request_id

        return response


def _valid_request_id(value: str | None) -> str | None:
    if not value:
        return None

    trimmed = value.strip()[:32]

    if not trimmed.replace("_", "").replace("-", "").isalnum():
        return None

    return trimmed


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Tell the browser what it may do with this response (Phase 5.9).

    EVERYTHING HERE DEFENDS AGAINST THE BROWSER BEING WRONG ABOUT YOUR
    INTENT - sniffing a content type, leaking a URL in a Referer,
    framing your approval dialog. None of it happens on the server, so
    none of it can be fixed by server logic. All you can do is say so
    in a header, and the headers cost nothing.
    """

    def __init__(self, app, settings: APISettings):
        super().__init__(app)

        self._settings = settings

    async def dispatch(self, request: Request, call_next):

        response = await call_next(request)

        # Never guess that JSON is HTML and run it. This is why
        # "upload a file called x.json containing <script>" is not an
        # attack on you.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # Your URLs carry agent ids and conversation ids. Without this
        # they leave in the Referer header on every external link a
        # user clicks.
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

        # Not on Phase5.md's list, and it belongs there. This is what
        # stops the approval dialog being clickjacked - an invisible
        # frame over a page the user thinks is something else, with
        # Approve exactly where they are about to click.
        response.headers.setdefault("X-Frame-Options", "DENY")

        # The API returns JSON, never a page. Nothing here needs a
        # camera, a microphone or a location.
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )

        # HSTS: PRODUCTION ONLY, and deliberately so.
        #
        # A browser that has seen this refuses http:// for the whole
        # max-age and there is no way to take it back - so sending it
        # from a dev server on localhost breaks local development for a
        # year, on every developer's machine, with no obvious cause.
        if self._settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response


class ErrorEnvelopeMiddleware(BaseHTTPMiddleware):
    """
    Turn an unhandled exception into the standard envelope (Phase 5.9).

    WHY A MIDDLEWARE AND NOT JUST @app.exception_handler(Exception)

    Because of WHERE Starlette runs that handler. The generic Exception
    handler belongs to ServerErrorMiddleware, which sits OUTSIDE every
    middleware you add - so the 500 it produces never passes back
    through them:

        no X-Content-Type-Options, no Referrer-Policy, no
        X-Request-ID - on the one response most likely to be
        inspected by somebody trying to break in.

    Registered FIRST, which makes it INNERMOST, so the response it
    returns flows outward through the rate limiter, the security
    headers and the request context exactly like any other.

    HTTPException and validation errors do not need this - they are
    handled by ExceptionMiddleware, which is already inside the user
    stack, which is why a 429 keeps its Retry-After header today.
    """

    async def dispatch(self, request: Request, call_next):

        try:
            return await call_next(request)

        except Exception:
            # Imported here rather than at module scope: api.errors
            # imports from api.request_context, which this module also
            # imports, and a top-level import would be circular.
            from api.errors import envelope

            logger.exception(
                "unhandled error on %s %s",
                request.method,
                request.url.path,
            )

            return JSONResponse(
                status_code=500,
                content=envelope(
                    "internal_error",
                    "Something went wrong. Quote the request id if you "
                    "report this.",
                ),
            )
