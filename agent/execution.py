from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .errors import ToolError

"""
Execution records (Phase 2.8).

Every tool call produces one of these, whether it succeeded, failed,
was denied, or was never attempted.

Why bother, when the agent only needs the result?

  - Debugging. "The agent did something weird" becomes a list of exact
    calls, arguments, durations and errors.
  - The UI. This is the data behind the execution timeline in
    AI_Agent_Hub.md section 13 - the panel that shows the user what the
    agent is doing while it works. That panel is most of what makes an
    agent feel trustworthy rather than magical.
  - Operations. Success rate, p95 latency and error mix per service all
    fall out of these records for free.

Building the record now costs almost nothing. Retrofitting telemetry
into an executor that never collected it is a rewrite.
"""


class ExecutionStatus(str, Enum):
    """
    The outcome of one tool call.

    DENIED is separate from FAILED on purpose. "The user said no" and
    "GitHub returned a 500" are both non-successes, but they mean
    completely different things: one is the system working correctly,
    the other is an incident. Collapsing them would make your future
    error dashboard lie to you.
    """

    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"


def new_execution_id() -> str:
    """
    A short unique id, e.g. "exec_9f2c14a7b3de".

    Prefixed because these ids end up in logs next to conversation ids
    and message ids, and a bare hex string tells you nothing about
    which is which at 2am.
    """

    return f"exec_{uuid.uuid4().hex[:12]}"


# Argument values longer than this are truncated in the stored record.
_MAX_VALUE_LENGTH = 120

# Argument names that must never be written to a log, in full or in
# part. Matched as substrings, case-insensitively.
_SENSITIVE_KEYS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private",
)


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Make tool arguments safe to store and print.

    Two separate problems, both worth taking seriously before this
    reaches a client:

      1. Secrets. Log a token once and it is in your log aggregator
         forever, usually somewhere you cannot delete it from.

      2. Volume. `slack_send_message` carries the entire message body;
         `google_drive_create_file` can carry a whole document. Storing
         those in an execution record turns a telemetry table into a
         copy of your user's content.

    Redaction belongs HERE, at the point the record is built, not at
    the point it is printed. Anything else relies on every future
    caller remembering, and one of them will not.
    """

    redacted: dict[str, Any] = {}

    for key, value in arguments.items():

        lowered = key.lower()

        if any(marker in lowered for marker in _SENSITIVE_KEYS):
            redacted[key] = "***redacted***"
            continue

        if isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
            redacted[key] = (
                value[:_MAX_VALUE_LENGTH]
                + f"... ({len(value)} chars)"
            )
            continue

        redacted[key] = value

    return redacted


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """
    One tool call, start to finish.

    This is the executor's return value. It is deliberately a record of
    what happened rather than just the result, so that nothing has to
    be reconstructed later from logs.
    """

    execution_id: str
    tool_name: str

    status: ExecutionStatus
    started_at: str
    duration_ms: float

    # How many times we actually called the server. 1 means no retry.
    attempts: int = 1

    server: str = ""
    namespace: str | None = None
    operation: str = ""
    risk_level: str = ""

    arguments: dict[str, Any] = field(default_factory=dict)

    result: Any = None
    error: ToolError | None = None

    # Set when a human was asked to confirm this call.
    approved_by_user: bool | None = None

    # Argument type fixes applied before the call, e.g.
    # "page_size: str -> integer". Recorded rather than silent: the
    # executor edited what the model asked for, and that should be
    # visible in the timeline.
    coercions: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status is ExecutionStatus.SUCCESS

    @property
    def retried(self) -> bool:
        return self.attempts > 1

    def to_payload(self) -> dict[str, Any]:
        """
        What gets handed back to the LLM as the tool message.

        Success and failure share one shape, so the model never has to
        guess how to read a tool result.
        """

        if self.error is not None:
            return self.error.to_tool_payload()

        if isinstance(self.result, dict):
            return self.result

        return {"success": True, "result": self.result}

    def to_dict(self) -> dict[str, Any]:
        """
        Serializable form: logs, the API layer, the timeline UI.

        This is the JSON shape Phase2.md section 2.8 asks for, plus the
        fields that turned out to matter once permissions and retries
        existed.
        """

        return {
            "execution_id": self.execution_id,
            "tool": self.tool_name,
            "server": self.server,
            "namespace": self.namespace,
            "operation": self.operation,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 2),
            "attempts": self.attempts,
            "arguments": self.arguments,
            "approved_by_user": self.approved_by_user,
            "coercions": list(self.coercions),
            "error": (
                self.error.to_dict()
                if self.error is not None
                else None
            ),
        }

    def summary_line(self) -> str:
        """
        One line for the console, in the style of AI_Agent_Hub.md 13.

            OK   github_search_issues            421ms
            FAIL google_drive_get_file           88ms  (not_found)
        """

        marker = {
            ExecutionStatus.SUCCESS: "OK  ",
            ExecutionStatus.FAILED: "FAIL",
            ExecutionStatus.DENIED: "DENY",
        }[self.status]

        line = (
            f"{marker} {self.tool_name:<38} "
            f"{self.duration_ms:>7.0f}ms"
        )

        if self.attempts > 1:
            line += f"  ({self.attempts} attempts)"

        if self.error is not None:
            line += f"  [{self.error.code.value}]"

        return line


def utc_now_iso() -> str:
    """
    Current UTC time as an ISO 8601 string.

    Always UTC, never local time. Execution records outlive the machine
    that produced them, and a timestamp without a timezone is a bug
    waiting for your first deployment in another region.
    """

    return datetime.now(timezone.utc).isoformat()
