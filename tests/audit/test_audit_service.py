from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.audit import (  # noqa: E402
    TRANSACTIONAL_ACTIONS,
    AuditAction,
    ResourceType,
)
from api.request_context import (  # noqa: E402
    current_request_id,
    new_request_id,
    reset_request_context,
    set_request_context,
)
from api.services import audit_service  # noqa: E402

"""
The audit trail (Phase 5.8).

LOGGING IS FOR YOU. AUDITING IS FOR SOMEONE ELSE, LATER, WHO DOES NOT
TRUST YOU.

Every test here follows from that: rows that cannot be rewritten, rows
that carry identifiers rather than content, and a writer that cannot
break the thing it records.
"""


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# Building a row
# ---------------------------------------------------------------------


def test_an_entry_carries_who_what_and_to_what():
    user = uuid.uuid4()
    agent = uuid.uuid4()

    row = audit_service.entry(
        AuditAction.SCOPE_GRANTED,
        user_id=user,
        actor_user_id=user,
        resource_type=ResourceType.AGENT,
        resource_id=agent,
        scope="github:*:write",
    )

    assert row.action == "scope.granted"
    assert row.resource_type == "agent"
    assert row.resource_id == agent
    assert row.meta == {"scope": "github:*:write"}


def test_the_action_is_stored_as_its_value():
    # Python 3.10: a `str, Enum` still inherits Enum.__str__, so a
    # missing override writes "AuditAction.SCOPE_GRANTED" into the
    # column while every == comparison keeps passing.
    row = audit_service.entry(AuditAction.LOGIN_FAILED)

    assert row.action == "login.failed"
    assert "AuditAction" not in row.action


def test_none_values_are_dropped_from_metadata():
    # Otherwise every row carries a dozen nulls, and "scope: null" on a
    # login is noise somebody has to learn to ignore.
    row = audit_service.entry(
        AuditAction.LOGIN_SUCCEEDED,
        scope=None,
        tool_name=None,
        user_agent="curl",
    )

    assert row.meta == {"user_agent": "curl"}


def test_the_ip_is_truncated_rather_than_raising():
    # A 200-character X-Forwarded-For would otherwise raise
    # StringDataRightTruncation and fail the thing being audited.
    row = audit_service.entry(
        AuditAction.LOGIN_FAILED,
        ip_address="1.2.3.4," * 60,
    )

    assert len(row.actor_ip) <= 45


def test_actor_and_tenant_are_separate_columns():
    """
    They diverge the moment anyone else can act on an account - and
    "a staff member opened this user's data" is the first line a
    security review looks for.
    """

    owner = uuid.uuid4()
    staff = uuid.uuid4()

    row = audit_service.entry(
        AuditAction.STAFF_ACCESS,
        user_id=owner,
        actor_user_id=staff,
        resource_type=ResourceType.USER,
        resource_id=owner,
    )

    assert row.user_id == owner
    assert row.actor_user_id == staff


# ---------------------------------------------------------------------
# The two ways to write
# ---------------------------------------------------------------------


def test_record_refuses_an_observational_action():
    """
    Using record() for a login would put the audit write inside the
    login's transaction - so a full audit table would become an
    outage. The check makes that a loud programming error rather than
    a quiet operational one.
    """

    try:
        audit_service.record(None, AuditAction.LOGIN_SUCCEEDED)
        raise AssertionError("an observational action was recorded")

    except ValueError as exc:
        assert "observe()" in str(exc)


def test_the_transactional_set_is_state_changes_only():
    # If the event IS the state change, they commit together. A scope
    # grant whose audit row is missing is a lie about permissions.
    for action in (
        AuditAction.SCOPE_GRANTED,
        AuditAction.SCOPE_REVOKED,
        AuditAction.APPROVAL_APPROVED,
        AuditAction.PLUGIN_CONNECTED,
    ):
        assert action in TRANSACTIONAL_ACTIONS

    # Observations must never be able to fail what they observe.
    for action in (
        AuditAction.LOGIN_SUCCEEDED,
        AuditAction.LOGIN_FAILED,
        AuditAction.TOOL_EXECUTED,
        AuditAction.APPROVAL_EXPIRED,
    ):
        assert action not in TRANSACTIONAL_ACTIONS


def test_observe_with_no_sessionmaker_is_a_no_op():
    # The CLI and the tests have nothing to write to, and refusing to
    # run would be absurd.
    run(audit_service.observe(None, AuditAction.LOGIN_SUCCEEDED))


def test_a_failed_observe_never_raises_but_does_alert():
    """
    Phase5.md rule 4, both halves.

    It must not fail the request - so it swallows. And a failure must
    raise an alert - so it logs at ERROR, because an audit log that
    stopped recording weeks ago and nobody noticed is worse than not
    having one.
    """

    import logging

    class Broken:
        def __call__(self):
            raise RuntimeError("database is gone")

    records: list[logging.LogRecord] = []

    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]

    logger = logging.getLogger("api.services.audit_service")
    logger.addHandler(handler)

    try:
        # Must not raise.
        run(audit_service.observe(Broken(), AuditAction.LOGIN_FAILED))

    finally:
        logger.removeHandler(handler)

    assert any(r.levelno >= logging.ERROR for r in records)


# ---------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------


def test_the_request_id_reaches_a_row_without_being_passed():
    """
    A service three layers down records the request id without every
    function on the way threading it through - the same ContextVar
    trick core/tenancy.py uses for credentials.
    """

    request_id = new_request_id()

    tokens = set_request_context(request_id, "9.9.9.9")

    try:
        row = audit_service.entry(AuditAction.SCOPE_GRANTED)

        assert row.request_id == request_id
        assert row.actor_ip == "9.9.9.9"

    finally:
        reset_request_context(tokens)


def test_the_context_is_reset_afterwards():
    # Without the reset, a request sharing a context with the next one
    # leaks its id into that one's rows - a quiet, permanent lie in the
    # table kept because it does not lie.
    tokens = set_request_context(new_request_id(), "1.1.1.1")
    reset_request_context(tokens)

    assert current_request_id() is None


def test_an_explicit_ip_beats_the_context():
    # A login records the address it was attempted from.
    tokens = set_request_context(new_request_id(), "1.1.1.1")

    try:
        row = audit_service.entry(
            AuditAction.LOGIN_FAILED, ip_address="2.2.2.2"
        )

        assert row.actor_ip == "2.2.2.2"

    finally:
        reset_request_context(tokens)


def test_request_ids_are_short_enough_to_read_aloud():
    # A user reads this off a screen and types it into a support
    # message. A 36-character UUID does not survive that trip.
    value = new_request_id()

    assert value.startswith("req_")
    assert len(value) <= 32


# ---------------------------------------------------------------------
# What must never be in here
# ---------------------------------------------------------------------


def test_the_vocabulary_covers_what_the_document_requires():
    values = {a.value for a in AuditAction}

    for required in (
        "login.succeeded",
        "login.failed",
        "logout",
        "scope.granted",
        "scope.revoked",
        "tool.enabled",
        "plugin.connected",
        "approval.requested",
        "approval.approved",
        "approval.denied",
        "approval.expired",
        "staff.access",
    ):
        assert required in values, required


def test_every_action_is_resource_dot_verb():
    # This column is what somebody greps at 2am. "scope.granted" next
    # to "granted_scope" next to "GRANT_SCOPE" means every search finds
    # two thirds of the answer.
    for action in AuditAction:
        assert action.value == action.value.lower()
        assert " " not in action.value
