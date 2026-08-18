from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.types import CallToolResult, TextContent  # noqa: E402

from agent.errors import ErrorCode  # noqa: E402
from agent.execution import ExecutionStatus, redact_arguments  # noqa: E402
from agent.executor import ToolExecutor  # noqa: E402
from agent.permissions import (  # noqa: E402
    DenyAll,
    MaxRiskPolicy,
    ReadOnlyPolicy,
    ScopePolicy,
)
from agent.schemas import RiskLevel  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Tests for the Phase 2.5 executor.

Uses the real `mcp.types.CallToolResult` rather than a stub, so these
tests verify the executor against the actual protocol shape - including
the `isError` alias and the text-content list - not against my
assumptions about it.
"""


REGISTRY = build_registry()


# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------


class FakeSession:
    """
    A scripted MCP session.

    Each entry in `responses` is either a CallToolResult to return or
    an Exception to raise. Records every call so tests can assert on
    how many attempts were made.

    Once the script runs out, the LAST entry repeats forever. That
    detail matters: an earlier version returned a default success
    instead, which made a retrying tool appear to succeed on attempt
    two and quietly turned a real failing test green.
    """

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None):

        self.calls.append((name, arguments or {}))

        if not self.responses:
            raise AssertionError(
                "FakeSession was called with no scripted responses"
            )

        item = (
            self.responses.pop(0)
            if len(self.responses) > 1
            else self.responses[0]
        )

        if isinstance(item, BaseException):
            raise item

        return item


def ok(payload) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload)),
        ],
        isError=False,
    )


def protocol_error(payload) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload)),
        ],
        isError=True,
    )


def run(coro):
    """
    Run one coroutine to completion.

    Written this way rather than with async test functions so the suite
    needs neither pytest-asyncio nor a custom runner.
    """

    return asyncio.run(coro)


def make_executor(**kwargs) -> ToolExecutor:
    # backoff_base=0 so retry tests do not actually sleep.
    kwargs.setdefault("backoff_base", 0.0)
    return ToolExecutor(REGISTRY, **kwargs)


# ---------------------------------------------------------------------
# 1-2. Resolving and gating the tool
# ---------------------------------------------------------------------


def test_unknown_tool_fails_without_calling_the_server():
    session = FakeSession()

    record = run(
        make_executor().execute(session, "github_does_not_exist", {})
    )

    assert record.status is ExecutionStatus.FAILED
    assert record.error.code is ErrorCode.TOOL_NOT_FOUND
    assert session.calls == [], "must not reach the network"


def test_tool_outside_the_routed_set_is_refused():
    # The router's decision is enforced, not merely advisory.
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {},
            allowed_tools={"slack_list_channels"},
        )
    )

    assert record.error.code is ErrorCode.TOOL_NOT_AVAILABLE
    assert session.calls == []


# ---------------------------------------------------------------------
# 3. Argument validation
# ---------------------------------------------------------------------


def test_missing_required_argument_is_caught_before_the_call():
    # github_get_repository requires owner and repo.
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor().execute(
            session,
            "github_get_repository",
            {"owner": "argus"},
        )
    )

    assert record.error.code is ErrorCode.INVALID_ARGUMENTS
    assert session.calls == [], "validation must run before the call"
    assert "repo" in record.error.message


def test_valid_arguments_pass_validation():
    session = FakeSession(ok({"success": True, "name": "test"}))

    record = run(
        make_executor().execute(
            session,
            "github_get_repository",
            {"owner": "argus", "repo": "test"},
        )
    )

    assert record.succeeded
    assert len(session.calls) == 1


def test_validation_can_be_disabled():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(validate_arguments=False).execute(
            session,
            "github_get_repository",
            {},
        )
    )

    assert record.succeeded


# ---------------------------------------------------------------------
# 4. Permissions
# ---------------------------------------------------------------------


def test_read_only_policy_blocks_writes():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(policy=ReadOnlyPolicy()).execute(
            session,
            "github_create_issue",
            {"owner": "a", "repo": "b", "title": "t"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert record.error.code is ErrorCode.PERMISSION_DENIED
    assert session.calls == []


def test_read_only_policy_allows_reads():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(policy=ReadOnlyPolicy()).execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.succeeded


def test_risk_ceiling_blocks_dangerous_tools():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(
            policy=MaxRiskPolicy(RiskLevel.MEDIUM)
        ).execute(
            session,
            "google_drive_delete_file",
            {"file_id": "x"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert session.calls == []


def test_scope_policy_accepts_a_wildcard_grant():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(
            policy=ScopePolicy({"github:*:read"})
        ).execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.succeeded


def test_scope_policy_denies_what_was_not_granted():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(
            policy=ScopePolicy({"github:*:read"})
        ).execute(
            session,
            "github_create_issue",
            {"owner": "a", "repo": "b", "title": "t"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert session.calls == []


# ---------------------------------------------------------------------
# 5. Approval
# ---------------------------------------------------------------------


def test_declined_approval_stops_the_call():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(approval=DenyAll()).execute(
            session,
            "slack_send_message",
            {"channel_id": "C1", "text": "hello"},
        )
    )

    assert record.status is ExecutionStatus.DENIED
    assert record.error.code is ErrorCode.APPROVAL_DENIED
    assert record.approved_by_user is False
    assert session.calls == []


def test_reads_are_never_sent_for_approval():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(approval=DenyAll()).execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.succeeded
    assert record.approved_by_user is None


# ---------------------------------------------------------------------
# 6-7. Detecting failure
# ---------------------------------------------------------------------


def test_application_error_hidden_in_a_successful_response():
    # THE case that makes a naive executor report a broken integration
    # as healthy. MCP says the call succeeded; the payload says it did
    # not.
    session = FakeSession(
        ok(
            {
                "success": False,
                "error": {
                    "type": "GitHubNotFoundError",
                    "message": "Not Found",
                    "status_code": 404,
                },
            }
        )
    )

    record = run(
        make_executor().execute(
            session,
            "github_get_repository",
            {"owner": "a", "repo": "nope"},
        )
    )

    assert record.status is ExecutionStatus.FAILED
    assert record.error.code is ErrorCode.NOT_FOUND


def test_protocol_level_error_flag_is_honoured():
    session = FakeSession(
        protocol_error({"error": "tool exploded"})
    )

    record = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.status is ExecutionStatus.FAILED


def test_plain_text_result_is_a_success_not_an_error():
    session = FakeSession(
        CallToolResult(
            content=[TextContent(type="text", text="all done")],
            isError=False,
        )
    )

    record = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.succeeded
    assert record.result == "all done"


# ---------------------------------------------------------------------
# 8. Retries
# ---------------------------------------------------------------------


def test_read_retries_after_a_timeout_and_succeeds():
    session = FakeSession(
        asyncio.TimeoutError(),
        asyncio.TimeoutError(),
        ok({"success": True, "items": []}),
    )

    record = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.succeeded
    assert record.attempts == 3
    assert len(session.calls) == 3


def test_write_is_NOT_retried_after_a_timeout():
    # The single most important test in this file.
    #
    # A timeout means the response was lost, not that the request
    # failed. GitHub may already have created the issue. Retrying
    # would create a second one, and the client would see the agent
    # duplicating their data.
    session = FakeSession(
        asyncio.TimeoutError(),
        ok({"success": True}),
    )

    record = run(
        make_executor().execute(
            session,
            "github_create_issue",
            {"owner": "a", "repo": "b", "title": "t"},
        )
    )

    assert not record.succeeded
    assert record.error.code is ErrorCode.TIMEOUT
    assert record.attempts == 1
    assert len(session.calls) == 1


def test_write_IS_retried_when_the_request_provably_never_landed():
    # A refused connection never reached GitHub, so repeating it
    # cannot duplicate anything.
    session = FakeSession(
        ConnectionRefusedError("no route"),
        ok({"success": True}),
    )

    record = run(
        make_executor().execute(
            session,
            "github_create_issue",
            {"owner": "a", "repo": "b", "title": "t"},
        )
    )

    assert record.succeeded
    assert record.attempts == 2


def test_rate_limit_is_retried_even_for_writes():
    # 429 is the service explicitly saying "I rejected this".
    session = FakeSession(
        ok(
            {
                "success": False,
                "error": {"message": "rate limited", "status_code": 429},
            }
        ),
        ok({"success": True}),
    )

    record = run(
        make_executor().execute(
            session,
            "github_create_issue",
            {"owner": "a", "repo": "b", "title": "t"},
        )
    )

    assert record.succeeded
    assert record.attempts == 2


def test_auth_failure_is_never_retried():
    session = FakeSession(
        ok(
            {
                "success": False,
                "error": {"message": "bad token", "status_code": 401},
            }
        ),
        ok({"success": True}),
    )

    record = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert not record.succeeded
    assert record.error.code is ErrorCode.AUTHENTICATION_FAILED
    assert record.attempts == 1


def test_attempts_are_capped():
    session = FakeSession(*[asyncio.TimeoutError() for _ in range(10)])

    record = run(
        make_executor(max_attempts=3).execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.attempts == 3
    assert len(session.calls) == 3


def test_cancellation_is_never_swallowed():
    # Cancellation means the caller gave up. Turning it into a
    # retryable error would keep doing work nobody is waiting for.
    session = FakeSession(asyncio.CancelledError())

    try:
        run(
            make_executor().execute(
                session,
                "github_list_issues",
                {"owner": "a", "repo": "b"},
            )
        )

    except (asyncio.CancelledError, RuntimeError):
        return

    raise AssertionError("CancelledError must propagate")


# ---------------------------------------------------------------------
# Execution records (Phase 2.8)
# ---------------------------------------------------------------------


def test_record_captures_the_full_metadata():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    )

    assert record.execution_id.startswith("exec_")
    assert record.tool_name == "github_list_issues"
    assert record.namespace == "github"
    assert record.operation == "read"
    assert record.risk_level == "safe"
    assert record.duration_ms >= 0.0
    assert record.started_at.endswith("+00:00"), "timestamps must be UTC"


def test_record_serializes_for_the_timeline_ui():
    session = FakeSession(ok({"success": True}))

    payload = run(
        make_executor().execute(
            session,
            "github_list_issues",
            {"owner": "a", "repo": "b"},
        )
    ).to_dict()

    for key in [
        "execution_id",
        "tool",
        "status",
        "started_at",
        "duration_ms",
        "attempts",
    ]:
        assert key in payload


def test_success_and_failure_share_one_payload_shape():
    session = FakeSession(
        ok(
            {
                "success": False,
                "error": {"message": "nope", "status_code": 404},
            }
        )
    )

    record = run(
        make_executor().execute(
            session,
            "github_get_repository",
            {"owner": "a", "repo": "b"},
        )
    )

    payload = record.to_payload()

    assert payload["success"] is False
    assert "hint" in payload["error"], (
        "the model needs to be told what to do next"
    )


# ---------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------


def test_secrets_are_redacted_from_records():
    redacted = redact_arguments(
        {
            "api_key": "sk-live-123456",
            "password": "hunter2",
            "owner": "argus",
        }
    )

    assert redacted["api_key"] == "***redacted***"
    assert redacted["password"] == "***redacted***"
    assert redacted["owner"] == "argus"


def test_long_values_are_truncated_not_stored_whole():
    redacted = redact_arguments({"text": "x" * 5000})

    assert len(redacted["text"]) < 200
    assert "5000 chars" in redacted["text"]


def test_redaction_happens_inside_the_executor():
    session = FakeSession(ok({"success": True}))

    record = run(
        make_executor(approval=None).execute(
            session,
            "slack_send_message",
            {"channel_id": "C1", "text": "y" * 500},
        )
    )

    # The record stores a truncated copy...
    assert "500 chars" in record.arguments["text"]

    # ...but the real argument still reached the server intact.
    assert len(session.calls[0][1]["text"]) == 500
