from __future__ import annotations

import secrets
from contextvars import ContextVar


"""
Who and where, for the current request (Phase 5.8).

An audit row three layers down needs the caller's IP and the request
id. Threading both through every service function would mean touching
every signature - and the one that forgets is the row with no context,
which is the row you need.

A ContextVar carries them instead. Same mechanism core/tenancy.py uses
to carry credentials into the MCP server, and it holds for the same
reason: it is per-task, so two concurrent requests cannot see each
other's values.
"""


_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_CLIENT_IP: ContextVar[str | None] = ContextVar("client_ip", default=None)


def new_request_id() -> str:
    """
    A short, greppable id.

    Prefixed and hex rather than a bare UUID: a user reads this off a
    screen and types it into a support message. "req_8f3a1c2b" survives
    that trip; a 36-character UUID with hyphens does not.
    """

    return f"req_{secrets.token_hex(4)}"


def set_request_context(request_id: str, ip: str | None) -> tuple:
    """
    Bind both, returning the reset tokens.

    The caller MUST reset in a finally. Setting without resetting means
    a request that shares a context with the next one leaks its id into
    that one's audit rows - which is a subtle, permanent lie in a table
    specifically kept because it does not lie.
    """

    return _REQUEST_ID.set(request_id), _CLIENT_IP.set(ip)


def reset_request_context(tokens: tuple) -> None:
    request_token, ip_token = tokens

    _REQUEST_ID.reset(request_token)
    _CLIENT_IP.reset(ip_token)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def current_ip() -> str | None:
    return _CLIENT_IP.get()


def log_fields() -> dict[str, str | None]:
    """
    The ambient context, shaped for a log record (Phase 6.5).

    Handed to core.logging.setup_logging(), which calls it once per log
    record and copies the result onto that record. The effect is that
    EVERY line produced while serving a request carries the request id,
    without a single logger.info() call being changed - and, more
    importantly, without any of them being able to forget.

    That is the whole value of a correlation id. One that is present on
    95% of lines is not 95% as useful; the missing 5% are exactly the
    lines you go looking for when something has gone wrong.

    Returns None values outside a request - a startup line, a
    background task - and the filter drops those rather than writing
    "request_id": null on every boot message.
    """

    return {
        "request_id": current_request_id(),
        "client_ip": current_ip(),
    }
