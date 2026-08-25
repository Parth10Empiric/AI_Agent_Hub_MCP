from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.classification import (  # noqa: E402
    build_permissions,
    classify_operation,
    classify_risk,
    split_tool_name,
)
from agent.schemas import Operation, RiskLevel  # noqa: E402
from tests.tool_fixtures import build_tool_definitions  # noqa: E402

"""
Tests for Phase 2.2 classification.

These run against the REAL tool set parsed out of `services/`, so they
also act as a safety net: add a new tool whose name the heuristic
cannot read, and `test_no_tool_falls_through_to_default` fails and
tells you to add an override.
"""


TOOLS = {tool.name: tool for tool in build_tool_definitions()}


# ---------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------


def test_split_tool_name_extracts_verb_and_resource():
    assert split_tool_name("github_create_issue", "github") == (
        "create",
        "issue",
    )


def test_split_tool_name_singularizes_resource():
    # Permission scopes must be stable: "github:issue:read", never a
    # mix of "issue" and "issues" meaning the same thing.
    verb, resource = split_tool_name(
        "google_drive_search_files",
        "google_drive",
    )
    assert verb == "search"
    assert resource == "file"


def test_split_tool_name_handles_missing_verb():
    verb, resource = split_tool_name(
        "slack_channel_history",
        "slack",
    )
    assert verb is None
    assert resource == "channel history"


# ---------------------------------------------------------------------
# Operation classification
# ---------------------------------------------------------------------


def test_read_verbs_classify_as_read():
    for name in [
        "github_list_issues",
        "google_drive_search_files",
        "google_calendar_get_event",
    ]:
        assert TOOLS[name].operation is Operation.READ
        assert TOOLS[name].read_only is True


def test_write_verbs_classify_as_write():
    for name in [
        "github_create_issue",
        "google_calendar_update_event",
        "slack_send_message",
    ]:
        assert TOOLS[name].operation is Operation.WRITE
        assert TOOLS[name].read_only is False


def test_delete_verbs_classify_as_delete():
    assert TOOLS["google_drive_delete_file"].operation is (
        Operation.DELETE
    )
    assert TOOLS["slack_delete_message"].operation is Operation.DELETE


def test_access_control_tools_classify_as_admin():
    # The distinction that a naive verb-only classifier misses:
    # create_file and create_permission are both "create", but only
    # one of them hands your data to someone else.
    assert TOOLS["google_drive_create_file"].operation is (
        Operation.WRITE
    )
    assert TOOLS["google_drive_create_permission"].operation is (
        Operation.ADMIN
    )


def test_verbless_tools_are_corrected_by_override():
    # These four have no leading verb. Without an override the
    # fail-closed default would mark them WRITE and force a pointless
    # approval prompt on every harmless read.
    for name in [
        "slack_auth_info",
        "slack_channel_history",
        "slack_thread_replies",
        "google_calendar_freebusy",
    ]:
        assert TOOLS[name].operation is Operation.READ
        assert TOOLS[name].classification_source == "override"


def test_unknown_verb_fails_closed_to_write():
    # The core safety property. Guessing READ on a tool that actually
    # writes means silent data loss; guessing WRITE on a read means one
    # unnecessary confirmation dialog. When uncertain, take the
    # harmless mistake.
    operation, source = classify_operation(
        tool_name="github_frobnicate_widget",
        namespace="github",
        annotations={},
    )
    assert operation is Operation.WRITE
    assert source == "default"


def test_no_tool_falls_through_to_default():
    # Every one of the real tools must be classified by an
    # override or the heuristic. A tool landing on "default" means the
    # classifier could not read its name - add an override.
    unclassified = [
        tool.name
        for tool in TOOLS.values()
        if tool.classification_source == "default"
    ]
    assert unclassified == []


# ---------------------------------------------------------------------
# Annotations take priority over guessing
# ---------------------------------------------------------------------


def test_mcp_annotations_beat_the_name_heuristic():
    # The name says "delete", but the server declared it read-only.
    # The server author is authoritative.
    operation, source = classify_operation(
        tool_name="github_delete_nothing",
        namespace="github",
        annotations={"readOnlyHint": True},
    )
    assert operation is Operation.READ
    assert source == "annotation"


def test_explicit_override_beats_annotations():
    operation, source = classify_operation(
        tool_name="slack_auth_info",
        namespace="slack",
        annotations={"readOnlyHint": False},
    )
    assert operation is Operation.READ
    assert source == "override"


# ---------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------


def test_risk_defaults_follow_operation():
    assert classify_risk("x_get_y", Operation.READ) is RiskLevel.SAFE
    assert classify_risk("x_add_y", Operation.WRITE) is (
        RiskLevel.MEDIUM
    )
    assert classify_risk("x_delete_y", Operation.DELETE) is (
        RiskLevel.HIGH
    )


def test_risk_is_not_derived_from_the_verb_alone():
    # Both are WRITE. Only one is irreversible and visible to other
    # people, and that difference is what risk is actually measuring.
    assert TOOLS["google_drive_create_file"].risk_level is (
        RiskLevel.MEDIUM
    )
    assert TOOLS["slack_send_message"].risk_level is RiskLevel.HIGH


def test_destroying_user_data_is_critical():
    assert TOOLS["google_drive_delete_file"].risk_level is (
        RiskLevel.CRITICAL
    )


def test_risk_severity_is_ordered():
    # Guards against comparing the str-enum values directly, where
    # "critical" < "safe" would be alphabetically True.
    assert RiskLevel.SAFE.severity < RiskLevel.MEDIUM.severity
    assert RiskLevel.MEDIUM.severity < RiskLevel.CRITICAL.severity


# ---------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------


def test_reads_do_not_require_approval():
    assert TOOLS["github_list_issues"].requires_approval is False


def test_mutating_tools_require_approval():
    for name in [
        "github_create_issue",
        "google_drive_delete_file",
        "google_drive_create_permission",
    ]:
        assert TOOLS[name].requires_approval is True


def test_high_risk_reads_still_require_approval():
    # A read can still be dangerous. The rule is "mutating OR high
    # risk", not "mutating" alone.
    from agent.schemas import ToolDefinition

    tool = ToolDefinition(
        name="x_export_everything",
        description="",
        input_schema={},
        server="s",
        operation=Operation.READ,
        risk_level=RiskLevel.HIGH,
    )
    assert tool.read_only is True
    assert tool.requires_approval is True


# ---------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------


def test_permissions_emit_specific_and_wildcard_scopes():
    scopes = build_permissions("github", "issue", Operation.WRITE)
    assert scopes == ("github:issue:write", "github:*:write")


def test_admin_operations_get_an_admin_scope():
    scopes = build_permissions(
        "google_drive",
        "permission",
        Operation.ADMIN,
    )
    assert scopes == (
        "google_drive:permission:admin",
        "google_drive:*:admin",
    )


def test_every_tool_has_permission_scopes():
    for tool in TOOLS.values():
        assert len(tool.permissions) == 2, tool.name
        assert tool.permissions[0].startswith(f"{tool.namespace}:")
