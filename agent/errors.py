from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .schemas import Operation

"""
Error taxonomy (Phase 2.6).

A tool call can fail in a dozen different ways, and the agent needs to
react differently to each one. "Something went wrong" is not enough
information to act on: a bad argument should be corrected and retried
immediately, an expired token should stop the agent and tell the user,
and a rate limit should wait and try again.

So the first job of this module is to turn every possible failure -
whatever shape it arrives in - into ONE structured value.

---------------------------------------------------------------------
A design decision that surprises people: errors here are VALUES, not
exceptions.
---------------------------------------------------------------------

The instinct is `raise ToolExecutionError(...)`. We deliberately do not.

An exception means "abandon this work and unwind the stack". But a
failed tool call is not abandoned work - the failure IS the answer, and
it has to be handed back to the LLM as a tool message so the model can
read it and self-correct:

    Agent: github_get_repository(owner="argus", repo="tset")
    Tool:  {"success": false, "error": {"type": "not_found", ...}}
    Agent: (notices the typo) github_get_repository(repo="test")

If we raised, that recovery loop could not exist. This is also why the
MCP specification itself says tool errors SHOULD be reported inside the
result with `isError`, not as protocol errors.

Exceptions are for "the program is broken". Values are for "the world
said no". A tool failure is the second kind.
"""


class ErrorCode(str, Enum):
    """
    Every distinct way a tool call can fail.

    Grouped by WHERE the failure happened, because that determines who
    can fix it: us, the network, or the remote service.
    """

    # --- Our side: caught before we ever call the server -------------
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_NOT_AVAILABLE = "tool_not_available"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"

    # THREE CODES, NOT ONE, and the distinction is for the human.
    #
    # All three end the same way - the call did not run - but they are
    # different events, and a UI that merges them tells people things
    # that are not true:
    #
    #   APPROVAL_DENIED       a person read the arguments and said no
    #   APPROVAL_EXPIRED      the question was asked and never answered
    #   APPROVAL_UNAVAILABLE  there was nobody to ask (a plain POST, a
    #                         CLI, a scheduled run)
    #
    # Merged, someone who clicked Deny is told "there was nobody to
    # answer", and someone whose scheduled job stalled is told they
    # refused something they never saw. Each needs a different next
    # step, so each gets its own code.
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_UNAVAILABLE = "approval_unavailable"

    # OUR limit, not the remote service's (Phase 5.7).
    #
    # Deliberately distinct from RATE_LIMITED, which is in
    # ALWAYS_RETRYABLE because a 429 from GitHub means "I did not run
    # this, try again". Our own budget is the opposite: retrying inside
    # the same window cannot succeed, and retrying is exactly what the
    # limit exists to prevent.
    BUDGET_EXCEEDED = "budget_exceeded"

    # --- Transport: the message never completed a round trip --------
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"
    SERVER_UNAVAILABLE = "server_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    CANCELLED = "cancelled"

    # --- Remote service: we reached it and it said no ---------------
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHORIZATION_FAILED = "authorization_failed"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    SERVICE_ERROR = "service_error"

    UNKNOWN = "unknown"


# ---------------------------------------------------------------------
# Retry classification (Phase 2.7)
# ---------------------------------------------------------------------
#
# Phase2.md gives the standard table: retry timeouts and connection
# errors, do not retry 401/403/invalid arguments. That table is correct
# but incomplete, and the missing piece is the one that causes real
# damage in production.
#
# The question is not only "might this succeed next time?" It is:
#
#       Do we KNOW the request never took effect?
#
# Consider `github_create_issue` timing out after 30 seconds. The
# timeout tells you the RESPONSE never arrived. It tells you nothing
# about whether the REQUEST was processed. GitHub may well have created
# the issue and simply been slow to answer. Retry, and your client gets
# two identical issues - and it looks like the agent is malfunctioning.
#
# So retryability has two tiers.

# Safe to retry for ANY operation: these prove the request was never
# processed. A refused connection never reached the service; a 429 is
# the service explicitly saying "I rejected this, I did not run it".
ALWAYS_RETRYABLE: frozenset[ErrorCode] = frozenset({
    ErrorCode.CONNECTION_ERROR,
    ErrorCode.SERVER_UNAVAILABLE,
    ErrorCode.RATE_LIMITED,
})

# Ambiguous: the request MIGHT have taken effect. Safe to retry only
# when repeating the call cannot cause harm - that is, for reads.
AMBIGUOUS_RETRYABLE: frozenset[ErrorCode] = frozenset({
    ErrorCode.TIMEOUT,
    ErrorCode.SERVICE_ERROR,
    ErrorCode.MALFORMED_RESPONSE,
})


def is_retryable(
    code: ErrorCode,
    operation: Operation = Operation.WRITE,
) -> bool:
    """
    Should we try this call again?

    Note the default: `Operation.WRITE`. If a caller forgets to say
    what kind of operation it was, we assume the dangerous one and
    retry less. Defaults should fail towards the harmless outcome, and
    here the harmless outcome is "do not repeat a possible write".

    Anything not explicitly listed is NOT retried. Retrying an error we
    do not understand is how you end up sending a Slack message three
    times.
    """

    if code in ALWAYS_RETRYABLE:
        return True

    if code in AMBIGUOUS_RETRYABLE:
        return operation is Operation.READ

    return False


# ---------------------------------------------------------------------
# Recovery hints for the LLM
# ---------------------------------------------------------------------
#
# When we hand an error back to the model, the error text alone is not
# very actionable. A short instruction attached to each code turns a
# dead end into a recovery step, and is the difference between an agent
# that retries the same broken call five times and one that fixes it.

RECOVERY_HINTS: dict[ErrorCode, str] = {
    ErrorCode.TOOL_NOT_FOUND: (
        "This tool does not exist. Use one of the tools you were "
        "given, or answer without tools."
    ),
    ErrorCode.TOOL_NOT_AVAILABLE: (
        "This tool was not selected for the current request. Use one "
        "of the tools you were given."
    ),
    ErrorCode.INVALID_ARGUMENTS: (
        "The arguments were rejected before the call was made. Read "
        "the error details, correct the arguments, and call again."
    ),
    ErrorCode.PERMISSION_DENIED: (
        "This agent is not permitted to use this tool. Do not retry. "
        "Tell the user what permission is missing."
    ),
    ErrorCode.APPROVAL_DENIED: (
        "The user declined this action. Do not retry it. Ask what "
        "they would like to do instead."
    ),
    ErrorCode.APPROVAL_EXPIRED: (
        "The user was asked to confirm this and did not answer in "
        "time. Do not retry it. Say that the request timed out and "
        "offer to try again."
    ),
    ErrorCode.APPROVAL_UNAVAILABLE: (
        "This action needs a person to confirm it and nobody could be "
        "asked. Do not retry it. Tell the user to ask again in the "
        "chat, where the confirmation prompt can appear."
    ),
    ErrorCode.AUTHENTICATION_FAILED: (
        "The connection to this service is not authenticated. Do not "
        "retry. Tell the user they need to reconnect the service."
    ),
    ErrorCode.AUTHORIZATION_FAILED: (
        "The account is authenticated but lacks access to this "
        "resource. Do not retry. Report this to the user."
    ),
    ErrorCode.NOT_FOUND: (
        "The requested resource does not exist. Check identifiers for "
        "typos, or search for the correct one first."
    ),
    ErrorCode.VALIDATION_ERROR: (
        "The service rejected the input. Correct it and try again."
    ),
    ErrorCode.BUDGET_EXCEEDED: (
        "This agent has reached its hourly limit for this kind of "
        "action. Tell the user plainly, say roughly when it resets, "
        "and do not retry."
    ),
    ErrorCode.RATE_LIMITED: (
        "The service is rate limiting us. This was already retried. "
        "Tell the user to try again shortly."
    ),
    ErrorCode.TIMEOUT: (
        "The call timed out. If this was a write operation it may "
        "still have succeeded - verify before repeating it."
    ),
    ErrorCode.SERVICE_ERROR: (
        "The remote service failed. This was already retried where "
        "safe. Report the failure to the user."
    ),
    ErrorCode.SERVER_UNAVAILABLE: (
        "The service is unavailable. Report this to the user."
    ),
    ErrorCode.CONNECTION_ERROR: (
        "Could not reach the service. Report this to the user."
    ),
    ErrorCode.MALFORMED_RESPONSE: (
        "The response could not be understood. Do not repeat the same "
        "call; try a different approach."
    ),
    ErrorCode.CANCELLED: "The call was cancelled.",
    ErrorCode.UNKNOWN: (
        "An unexpected error occurred. Do not repeat the same call."
    ),
}


@dataclass(frozen=True, slots=True)
class ToolError:
    """
    One normalized failure.

    Frozen because an error record is evidence. Once created it
    describes something that already happened, and nothing downstream
    should be able to edit history.
    """

    code: ErrorCode
    message: str

    # Where it came from: "agent" (we rejected it), "transport" (the
    # connection), or "service" (the remote API said no). Useful when
    # triaging: a spike in "transport" is your problem, a spike in
    # "service" is theirs.
    source: str = "agent"

    status_code: int | None = None

    # Seconds to wait before retrying, when the service tells us.
    retry_after: float | None = None

    details: dict[str, Any] = field(default_factory=dict)

    @property
    def hint(self) -> str:
        return RECOVERY_HINTS.get(self.code, "")

    def retryable(
        self,
        operation: Operation = Operation.WRITE,
    ) -> bool:
        return is_retryable(self.code, operation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.code.value,
            "message": self.message,
            "source": self.source,
            "status_code": self.status_code,
            "details": self.details,
        }

    @property
    def detail(self) -> str:
        """
        What the REMOTE SERVICE said, in its own words.

        Our `message` is a category ("GitHub rejected the request
        because the supplied data is invalid"). The service's message
        is the part that says how to fix it:

            "Query must include 'is:issue' or 'is:pull-request'"

        Empty when the failure was ours, or when the service said
        nothing useful.
        """

        details = self.details

        if not isinstance(details, dict):
            return ""

        message = details.get("message")

        if not isinstance(message, str):
            return ""

        message = message.strip()

        if not message or message == self.message:
            return ""

        # Bounded: this goes into the model's context on every failure,
        # and a service that returns a wall of text should not be able
        # to spend the whole budget.
        return message[:300]

    def to_tool_payload(self) -> dict[str, Any]:
        """
        The JSON we hand back to the LLM as the tool result.

        Deliberately keeps the same `{"success": false, "error": {...}}`
        shape your MCP tools already use, so the model sees one
        consistent contract whether the failure came from GitHub, from
        the network, or from our own permission check.

        `detail` CARRIES THE SERVICE'S OWN MESSAGE, and leaving it out
        was a real and expensive bug.

        GitHub refused a search with "Query must include 'is:issue' or
        'is:pull-request'" - a complete set of instructions for fixing
        the call. That sentence was parsed, stored on the execution
        record, and then dropped here. The model was told only "the
        supplied data is invalid", could not know WHAT was invalid, and
        gave up on a task it was one corrected argument away from
        finishing.

        The agent loop exists to let a model recover from a failed
        call. It cannot recover from an error that does not say what
        went wrong.
        """

        error: dict[str, Any] = {
            "type": self.code.value,
            "message": self.message,
            "hint": self.hint,
        }

        detail = self.detail

        if detail:
            error["detail"] = detail

        return {
            "success": False,
            "error": error,
        }


# ---------------------------------------------------------------------
# Classifying exceptions (transport-level failures)
# ---------------------------------------------------------------------


def classify_exception(exc: BaseException) -> ToolError:
    """
    Turn a raised exception into a ToolError.

    These are failures where the tool never got to answer: the socket
    died, the server never replied, the process went away.

    `asyncio.CancelledError` is NOT handled here. Cancellation means
    someone shut the task down deliberately - the user pressed Ctrl+C,
    or the web request was abandoned. Swallowing it and turning it into
    a retryable error would keep work running that the caller already
    gave up on. The executor re-raises it instead.
    """

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ToolError(
            code=ErrorCode.TIMEOUT,
            message="The tool call timed out.",
            source="transport",
        )

    if isinstance(exc, (BrokenPipeError, EOFError)):
        return ToolError(
            code=ErrorCode.SERVER_UNAVAILABLE,
            message="The MCP server closed the connection.",
            source="transport",
        )

    # NOTE: BrokenPipeError is a subclass of
    # ConnectionError, so it must be tested BEFORE this branch or
    # it would never be reached. Always order isinstance checks
    # from most specific to most general.
    if isinstance(exc, ConnectionError):
        return ToolError(
            code=ErrorCode.CONNECTION_ERROR,
            message=f"Could not reach the MCP server: {exc}",
            source="transport",
        )

    if isinstance(exc, json.JSONDecodeError):
        return ToolError(
            code=ErrorCode.MALFORMED_RESPONSE,
            message="The tool returned a response we could not parse.",
            source="transport",
        )

    if isinstance(exc, OSError):
        return ToolError(
            code=ErrorCode.CONNECTION_ERROR,
            message=f"Transport failure: {exc}",
            source="transport",
        )

    return ToolError(
        code=ErrorCode.UNKNOWN,
        message=f"{type(exc).__name__}: {exc}",
        source="transport",
        details={"exception": type(exc).__name__},
    )


# ---------------------------------------------------------------------
# Classifying application errors (the service said no)
# ---------------------------------------------------------------------


# HTTP status -> our vocabulary. The most reliable signal when present.
_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.AUTHENTICATION_FAILED,
    403: ErrorCode.AUTHORIZATION_FAILED,
    404: ErrorCode.NOT_FOUND,
    408: ErrorCode.TIMEOUT,
    409: ErrorCode.VALIDATION_ERROR,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.SERVICE_ERROR,
    502: ErrorCode.SERVER_UNAVAILABLE,
    503: ErrorCode.SERVER_UNAVAILABLE,
    504: ErrorCode.TIMEOUT,
}


# Fallback: match words in the error type or message.
#
# ORDER MATTERS. "authorization" and "authentication" both contain
# "auth", so the more specific patterns must be tested first or every
# permission error would be misread as an auth failure - and the agent
# would tell the user to reconnect their account when the real problem
# is a missing scope.
_KEYWORD_CODES: tuple[tuple[tuple[str, ...], ErrorCode], ...] = (
    (("ratelimit", "rate_limit", "rate limit", "too many"),
     ErrorCode.RATE_LIMITED),
    (("unavailable", "503"), ErrorCode.SERVER_UNAVAILABLE),
    (("timeout", "timed out"), ErrorCode.TIMEOUT),
    (("permission", "forbidden", "authorization", "not authorized",
      "access denied", "missing scope"),
     ErrorCode.AUTHORIZATION_FAILED),
    (("authentication", "unauthenticated", "invalid token",
      "invalid_auth", "expired token", "credential"),
     ErrorCode.AUTHENTICATION_FAILED),
    (("notfound", "not_found", "not found", "does not exist",
      "channel_not_found", "missing"),
     ErrorCode.NOT_FOUND),
    (("validation", "invalid", "bad request", "malformed"),
     ErrorCode.VALIDATION_ERROR),
    (("server error", "internal error", "servererror"),
     ErrorCode.SERVICE_ERROR),
)


def _code_from_text(text: str) -> ErrorCode | None:

    lowered = text.lower()

    for keywords, code in _KEYWORD_CODES:
        for keyword in keywords:
            if keyword in lowered:
                return code

    return None


def classify_payload(payload: Any) -> ToolError | None:
    """
    Inspect a SUCCESSFUL tool response for an application-level error.

    This function exists because of something specific about your
    server, and it is worth understanding clearly.

    Your tool wrappers (`@github_tool`, `@slack_tool`,
    `handle_calendar_errors`) catch their own exceptions and RETURN a
    dictionary. From the MCP protocol's point of view that call
    succeeded perfectly - status fine, no `isError` flag. The failure
    is hiding inside the payload:

        {"success": false, "error": {...}}

    If the executor only checked for exceptions and `isError`, it would
    record every GitHub 404 as a success, retry nothing, and report
    healthy metrics for a completely broken integration.

    Your four services also disagree on the shape, which is exactly why
    normalizing at the boundary is worth the effort:

        github    error = {"type", "message", "status_code", "details"}
        calendar  error = {"type", "message"}
        slack     error = "a plain string"
        drive     error = "a plain string" + a separate "code" field

    Returns None when the payload shows no error.
    """

    if not isinstance(payload, dict):
        return None

    if payload.get("success") is not False:
        return None

    raw_error = payload.get("error")

    message = "The tool reported a failure."
    error_type = ""
    status_code: int | None = None
    details: dict[str, Any] = {}

    if isinstance(raw_error, dict):
        message = str(
            raw_error.get("message")
            or raw_error.get("error")
            or message
        )
        error_type = str(raw_error.get("type") or "")

        raw_status = raw_error.get("status_code")

        if isinstance(raw_status, int):
            status_code = raw_status

        raw_details = raw_error.get("details")

        if isinstance(raw_details, dict):
            details = raw_details

    elif isinstance(raw_error, str):
        message = raw_error

    # Drive puts its code in a sibling field rather than inside
    # `error`, so look there too before giving up.
    if not error_type:
        error_type = str(payload.get("code") or "")

    code: ErrorCode | None = None

    if status_code is not None:
        code = _STATUS_CODES.get(status_code)

        if code is None and status_code >= 500:
            code = ErrorCode.SERVICE_ERROR

    if code is None and error_type:
        code = _code_from_text(error_type)

    if code is None:
        code = _code_from_text(message)

    if code is None:
        # Understood that it failed, but not why. UNKNOWN is not
        # retryable, which is the safe default - see `is_retryable`.
        code = ErrorCode.UNKNOWN

    retry_after: float | None = None
    raw_retry_after = details.get("retry_after")

    if isinstance(raw_retry_after, (int, float)):
        retry_after = float(raw_retry_after)

    return ToolError(
        code=code,
        message=message,
        source="service",
        status_code=status_code,
        retry_after=retry_after,
        details=details,
    )
