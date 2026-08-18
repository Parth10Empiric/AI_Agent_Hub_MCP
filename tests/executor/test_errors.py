from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.errors import (  # noqa: E402
    ALWAYS_RETRYABLE,
    AMBIGUOUS_RETRYABLE,
    ErrorCode,
    ToolError,
    classify_exception,
    classify_payload,
    is_retryable,
)
from agent.schemas import Operation  # noqa: E402

"""
Tests for the Phase 2.6 error taxonomy and the Phase 2.7 retry policy.

The retry tests are the important ones. Getting retries wrong does not
raise an exception or fail loudly - it quietly creates duplicate GitHub
issues and sends the same Slack message twice.
"""


# ---------------------------------------------------------------------
# Exception classification (transport failures)
# ---------------------------------------------------------------------


def test_timeout_is_classified():
    error = classify_exception(asyncio.TimeoutError())
    assert error.code is ErrorCode.TIMEOUT
    assert error.source == "transport"


def test_connection_error_is_classified():
    error = classify_exception(ConnectionRefusedError("refused"))
    assert error.code is ErrorCode.CONNECTION_ERROR


def test_broken_pipe_means_server_unavailable():
    error = classify_exception(BrokenPipeError())
    assert error.code is ErrorCode.SERVER_UNAVAILABLE


def test_bad_json_means_malformed_response():
    try:
        json.loads("{not json")
    except json.JSONDecodeError as exc:
        error = classify_exception(exc)

    assert error.code is ErrorCode.MALFORMED_RESPONSE


def test_unknown_exception_is_not_silently_lost():
    error = classify_exception(RuntimeError("something odd"))
    assert error.code is ErrorCode.UNKNOWN
    assert "RuntimeError" in error.message


# ---------------------------------------------------------------------
# Payload classification (the service said no)
# ---------------------------------------------------------------------


def test_successful_payload_is_not_an_error():
    assert classify_payload({"success": True, "items": []}) is None
    assert classify_payload({"anything": 1}) is None
    assert classify_payload("plain text result") is None


def test_github_style_nested_error_with_status():
    # {"success": false, "error": {"type", "message", "status_code"}}
    error = classify_payload(
        {
            "success": False,
            "error": {
                "type": "GitHubNotFoundError",
                "message": "Not Found",
                "status_code": 404,
            },
        }
    )

    assert error is not None
    assert error.code is ErrorCode.NOT_FOUND
    assert error.status_code == 404
    assert error.source == "service"


def test_slack_style_flat_string_error():
    # Slack's wrapper returns error as a plain string, not a dict.
    error = classify_payload(
        {
            "success": False,
            "error": "invalid_auth: token is not valid",
        }
    )

    assert error is not None
    assert error.code is ErrorCode.AUTHENTICATION_FAILED


def test_drive_style_sibling_code_field():
    # Drive puts its code beside `error`, not inside it.
    error = classify_payload(
        {
            "success": False,
            "error": "Internal tool error",
            "code": "INTERNAL_ERROR",
        }
    )

    assert error is not None
    # Nothing identifiable in either field, so UNKNOWN - and UNKNOWN
    # is deliberately not retryable.
    assert error.code is ErrorCode.UNKNOWN
    assert not error.retryable(Operation.READ)


def test_calendar_style_typed_error_without_status():
    error = classify_payload(
        {
            "success": False,
            "error": {
                "type": "PermissionError",
                "message": "The caller does not have permission",
            },
        }
    )

    assert error is not None
    assert error.code is ErrorCode.AUTHORIZATION_FAILED


def test_authorization_is_not_confused_with_authentication():
    # Both words contain "auth". If the keyword order were wrong, a
    # missing scope would be reported as an expired login and the
    # agent would tell the user to reconnect a perfectly good account.
    permission = classify_payload(
        {
            "success": False,
            "error": {"type": "PermissionError", "message": "denied"},
        }
    )
    authentication = classify_payload(
        {
            "success": False,
            "error": {
                "type": "AuthenticationError",
                "message": "bad token",
            },
        }
    )

    assert permission.code is ErrorCode.AUTHORIZATION_FAILED
    assert authentication.code is ErrorCode.AUTHENTICATION_FAILED


def test_status_codes_map_to_the_right_meaning():
    cases = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.AUTHENTICATION_FAILED,
        403: ErrorCode.AUTHORIZATION_FAILED,
        404: ErrorCode.NOT_FOUND,
        429: ErrorCode.RATE_LIMITED,
        500: ErrorCode.SERVICE_ERROR,
        503: ErrorCode.SERVER_UNAVAILABLE,
    }

    for status, expected in cases.items():
        error = classify_payload(
            {
                "success": False,
                "error": {"message": "x", "status_code": status},
            }
        )
        assert error.code is expected, status


def test_unmapped_5xx_still_counts_as_a_service_error():
    error = classify_payload(
        {
            "success": False,
            "error": {"message": "x", "status_code": 507},
        }
    )
    assert error.code is ErrorCode.SERVICE_ERROR


# ---------------------------------------------------------------------
# Retry policy - the part that causes real damage when wrong
# ---------------------------------------------------------------------


def test_never_processed_errors_are_retryable_for_any_operation():
    # A refused connection or a 429 proves the request never ran, so
    # repeating it cannot duplicate anything.
    for code in ALWAYS_RETRYABLE:
        assert is_retryable(code, Operation.READ)
        assert is_retryable(code, Operation.WRITE)
        assert is_retryable(code, Operation.DELETE)


def test_ambiguous_errors_are_retried_only_for_reads():
    # A timeout tells you the RESPONSE never arrived. It says nothing
    # about whether the request took effect. Retrying a timed-out
    # create_issue is how you get two identical issues.
    for code in AMBIGUOUS_RETRYABLE:
        assert is_retryable(code, Operation.READ)
        assert not is_retryable(code, Operation.WRITE)
        assert not is_retryable(code, Operation.DELETE)
        assert not is_retryable(code, Operation.ADMIN)


def test_client_errors_are_never_retried():
    for code in [
        ErrorCode.AUTHENTICATION_FAILED,
        ErrorCode.AUTHORIZATION_FAILED,
        ErrorCode.NOT_FOUND,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INVALID_ARGUMENTS,
        ErrorCode.PERMISSION_DENIED,
        ErrorCode.APPROVAL_DENIED,
    ]:
        assert not is_retryable(code, Operation.READ), code


def test_unknown_errors_are_not_retried():
    # Retrying something we do not understand is how a Slack message
    # gets sent three times.
    assert not is_retryable(ErrorCode.UNKNOWN, Operation.READ)


def test_retry_default_assumes_the_dangerous_operation():
    # A caller that forgets to pass the operation should get the
    # conservative answer, not the permissive one.
    assert is_retryable(ErrorCode.TIMEOUT, Operation.READ)
    assert not is_retryable(ErrorCode.TIMEOUT)


# ---------------------------------------------------------------------
# What the LLM receives
# ---------------------------------------------------------------------


def test_error_payload_keeps_the_shape_the_model_already_knows():
    error = ToolError(
        code=ErrorCode.NOT_FOUND,
        message="Repository not found",
    )

    payload = error.to_tool_payload()

    assert payload["success"] is False
    assert payload["error"]["type"] == "not_found"
    assert payload["error"]["message"] == "Repository not found"


def test_every_error_code_carries_a_recovery_hint():
    # The hint is what turns a dead end into a next step. A code
    # without one leaves the model guessing.
    for code in ErrorCode:
        error = ToolError(code=code, message="x")
        assert error.hint, f"{code.value} has no recovery hint"


def test_hints_tell_the_model_when_not_to_retry():
    auth = ToolError(
        code=ErrorCode.AUTHENTICATION_FAILED,
        message="x",
    )
    assert "not retry" in auth.hint.lower()
