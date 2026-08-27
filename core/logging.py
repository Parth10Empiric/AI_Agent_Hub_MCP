from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Callable

from config.settings import settings


"""
Logging that can be QUERIED, not just read (Phase 6.5).

WHY A STRING IS NOT ENOUGH

    logger.info(f"Tool {name} took {ms}ms")

is readable by one person watching one terminal, and useless at every
other moment. It cannot answer "every failed Drive call for user 42
that took over 200ms", because the fields were thrown away the instant
the f-string was built. Reconstructing them means regular expressions
over log text, which break the first time somebody rewords a message.

    logger.info("tool_executed", extra={"tool": name, "duration_ms": ms})

keeps them. The message becomes a stable EVENT NAME and the variables
become columns.

TWO FORMATS, ON PURPOSE

    development   the old human format - aligned, coloured by the
                  terminal, readable at a glance
    production    one JSON object per line, for a log shipper

JSON in a development terminal is miserable to read, and a developer
who cannot read their own logs turns logging off. The format follows
the environment so that neither audience has to compromise.

STDOUT IS FORBIDDEN. THIS IS NOT A STYLE CHOICE.

server.py speaks MCP JSON-RPC over stdio, which means STDOUT IS A
PROTOCOL STREAM. One log line written there is a corrupt frame, and
the failure surfaces as an unrelated MCP handshake error somewhere far
from the cause. Everything here writes to stderr, which the parent
process captures separately.
"""


# The attributes every LogRecord carries. Anything NOT in this set was
# put there by a caller - via `extra={...}` or by a Filter - and is
# therefore a field worth emitting.
#
# Kept as an explicit set rather than diffing against a fresh record,
# because that diff would run on every single log line.
_STANDARD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",
    }
)


# Substring match on the FIELD NAME, lowercased.
#
# The formatter emits arbitrary caller-supplied fields, which is the
# point - and also means a careless `extra={"access_token": ...}` would
# be written to disk in clear text. SecretStr protects the settings
# object; nothing protects an ad-hoc dict, so the formatter refuses at
# the last possible moment.
#
# Substring rather than exact match so "access_token", "refresh_token"
# and "token" are all caught by one entry.
_SECRET_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
)

_REDACTED = "***redacted***"


def _is_secret(field: str) -> bool:
    lowered = field.lower()

    return any(hint in lowered for hint in _SECRET_HINTS)


class ContextFilter(logging.Filter):
    """
    Attach ambient context to EVERY record, with no call-site changes.

    A Filter runs on every record on its way to a handler, so this is
    what turns `request_id` from something each logger.info() would
    have to remember into something that simply cannot be missing.
    Forgetting it on the one line that mattered is precisely how a
    correlation id fails to correlate.

    WHY IT TAKES A CALLABLE INSTEAD OF IMPORTING THE CONTEXTVARS

    core/ must not import from api/. server.py runs as a SUBPROCESS
    with no HTTP request and no api package concern, and it uses this
    same module. Passing a provider keeps the dependency pointing the
    right way: api wires its request context in, core knows nothing
    about it.
    """

    def __init__(self, provider: Callable[[], dict[str, Any]]) -> None:
        super().__init__()

        self._provider = provider

    def filter(self, record: logging.LogRecord) -> bool:

        try:
            fields = self._provider()

        except Exception:
            # A logging filter must NEVER raise. An exception here
            # happens inside the handler, while some other error is
            # already being reported - and it would replace that error
            # with a confusing one from the logging machinery itself.
            return True

        for key, value in fields.items():
            if value is not None:
                setattr(record, key, value)

        # Always True: this filter enriches, it never suppresses.
        return True


class JsonFormatter(logging.Formatter):
    """
    One JSON object per line.

    Line-delimited rather than a JSON array, because logs are a stream
    with no end - an array could only be closed when the process dies,
    and nothing could parse it until then. Every log shipper in
    existence reads one object per line.
    """

    def format(self, record: logging.LogRecord) -> str:

        payload: dict[str, Any] = {
            # ISO-8601 in UTC. Sorting log lines from three containers
            # in two timezones is not a problem anybody should have.
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S")
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Everything a caller added via extra={...} or a Filter.
        # Shared with TextFormatter so the two formats cannot drift
        # into showing different fields.
        payload.update(_extra_fields(record))

        if record.exc_info:
            # Rendered into the SAME line rather than printed after it.
            # A traceback written as 30 separate lines becomes 30
            # separate log entries in any shipper, 29 of which have no
            # level, no logger and no request id.
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str so an un-serialisable value degrades to its repr
        # instead of raising inside the handler and losing the line.
        return json.dumps(payload, default=str, ensure_ascii=False)


TEXT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The caller-supplied fields on a record, redacted."""

    return {
        key: _REDACTED if _is_secret(key) else value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRS
        and not key.startswith("_")
        and key != "color_message"
    }


class TextFormatter(logging.Formatter):
    """
    The human format, with the structured fields appended.

    WHY THIS IS NOT JUST logging.Formatter(TEXT_FORMAT)

    Moving to event-name-plus-fields makes messages DELIBERATELY
    uninformative on their own: "http_request" says nothing without its
    method, path and status. A plain %(message)s formatter drops every
    one of those, so the development terminal - which used to show
    uvicorn's access line - would show a column of identical
    "http_request" entries instead. Strictly less than before.

    So the same fields the JSON formatter emits as keys are appended
    here as `key=value`, and both audiences get the data.
    """

    def format(self, record: logging.LogRecord) -> str:

        base = super().format(record)

        fields = _extra_fields(record)

        if not fields:
            return base

        return base + " | " + " ".join(
            f"{key}={value}" for key, value in fields.items()
        )


def _use_json() -> bool:
    """
    JSON when explicitly asked, or when this is production.

    MCP_LOG_FORMAT is checked first so the choice can be forced either
    way - to read real JSON locally while developing this file, or to
    fall back to text during an incident when a human is tailing the
    logs directly.
    """

    explicit = os.getenv("MCP_LOG_FORMAT", "").strip().lower()

    if explicit in {"json", "text"}:
        return explicit == "json"

    return settings.environment.strip().lower() == "production"


def setup_logging(
    context_provider: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """
    Configure application logging.

    `context_provider` is called once per log record and its fields are
    attached to that record. api/main.py passes the request-context
    accessors; server.py passes nothing.
    """

    level = getattr(
        logging,
        settings.log_level.upper(),
        logging.INFO,
    )

    handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(
        JsonFormatter() if _use_json() else TextFormatter(TEXT_FORMAT)
    )

    if context_provider is not None:
        # On the HANDLER, not the logger. A filter on a logger does not
        # run for records that propagate up from its children, so
        # attaching it to the root logger would silently skip every
        # module's log line - which is all of them.
        handler.addFilter(ContextFilter(context_provider))

    # force=True REPLACES any handlers already installed.
    #
    # Required because uvicorn configures logging BEFORE it imports the
    # application. Without this, basicConfig sees existing handlers,
    # does nothing at all, and every line comes out in uvicorn's format
    # while this function appears to have worked.
    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,
    )

    # uvicorn does not use the root logger.
    #
    # It installs its own handlers on these three and sets
    # propagate=False, so they bypass everything configured above. The
    # result is a log stream that is half JSON and half plain text -
    # which is worse than plain text, because a shipper silently drops
    # the half it cannot parse and the gap is invisible.
    #
    # Clearing the handlers and re-enabling propagation routes them
    # through the root handler like everything else.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)

        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # uvicorn.access is SILENCED, not merely reformatted.
    #
    # It fires in the ASGI layer OUTSIDE the application middleware, so
    # by then RequestContextMiddleware has already reset the ContextVar
    # and the line carries no request id - the one field that makes an
    # access log worth having. It is also a preformatted string, so
    # method, path and status cannot be queried as fields.
    #
    # api/middleware.py logs "http_request" instead, from inside the
    # request context, with all of that as columns. Leaving both on
    # would mean two access lines per request, one of them worse.
    logging.getLogger("uvicorn.access").disabled = True

    # Route warnings.warn() through logging.
    #
    # Otherwise a library warning is written to stderr RAW - no level,
    # no timestamp, no JSON - and in production it lands in the middle
    # of the JSON stream as an unparseable fragment that the shipper
    # drops silently. google.api_core emits exactly such a warning on
    # every start of this project.
    logging.captureWarnings(True)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for a module."""

    return logging.getLogger(name)


# Enabled at IMPORT, not inside setup_logging().
#
# A warning raised while a module is being imported is raised before
# any function in this file has had the chance to run. google.api_core
# emits its Python-3.10 deprecation notice the moment it is imported,
# which in server.py happens at the top of the file - so a capture
# switched on inside setup_logging() would arrive too late for the one
# warning this project actually produces.
logging.captureWarnings(True)
