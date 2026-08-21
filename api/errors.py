from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.request_context import current_request_id
from core.logging import get_logger


logger = get_logger(__name__)


"""
One error shape, for every failure (Phase 5.9).

The frontend has read this since Phase 4:

    this.code = body?.error?.code;
    this.requestId = body?.error?.request_id;

and the backend has never produced it. A 500 returned FastAPI's
default {"detail": "Internal Server Error"} and both fields were
undefined - so half the error vocabulary from Phase 3.10 was wired to
nothing.

TWO RULES, PULLING AGAINST EACH OTHER

    Say as little as possible to the caller. A stack trace or a raw
    database error in a browser payload is an information leak, and
    "column users.password_hash does not exist" tells an attacker the
    schema.

    Say everything in the log. The person debugging this needs the
    exception, and they are not the person holding the response.

`request_id` is what reconciles them. The user quotes req_8f3a, and
that one string finds the log line, the stack trace, and every audit
row from the same request.
"""


def envelope(
    code: str,
    message: str,
    details: dict | None = None,
) -> dict:
    """The single response shape. Nothing else is returned on error."""

    body: dict = {
        "error": {
            "code": code,
            "message": message,
            "request_id": current_request_id(),
        }
    }

    if details:
        body["error"]["details"] = details

    return body


def install_error_handlers(app: FastAPI) -> None:
    """Wire the three handlers. Called from create_app."""

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        """
        A deliberate refusal - 404, 409, 429.

        `detail` is preserved as the message because every one of them
        was written to be read by a user: "Too many sign-in attempts.
        Try again in 15 minutes." Headers are preserved too, or a 429
        would lose its Retry-After.
        """

        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(
                _CODES.get(exc.status_code, "error"),
                str(exc.detail),
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        """
        422 from Pydantic.

        The field errors ARE safe to return - they describe the request
        the caller just sent, and a client that cannot see which field
        was wrong cannot fix it. Trimmed to the first few so a
        malformed bulk payload does not answer with a thousand lines.
        """

        return JSONResponse(
            status_code=422,
            content=envelope(
                "validation_error",
                "Some of the values sent were not valid.",
                {"fields": exc.errors()[:5]},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        """
        Anything that got this far is a bug.

        The message is DELIBERATELY vague. The exception, the traceback
        and the path go to the log, where the person who can act on
        them will look - tied to the same request_id the user was
        given.
        """

        logger.exception(
            "unhandled error on %s %s (request_id=%s)",
            request.method,
            request.url.path,
            current_request_id(),
        )

        return JSONResponse(
            status_code=500,
            content=envelope(
                "internal_error",
                "Something went wrong. Quote the request id if you "
                "report this.",
            ),
        )


# HTTP status -> the vocabulary the frontend already knows.
_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}
