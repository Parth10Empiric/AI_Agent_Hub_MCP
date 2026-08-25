from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.scopes import (  # noqa: E402
    InvalidScope,
    default_scopes,
    parse_scope,
    scope_catalog,
    tools_permitted_by,
    validate_scope,
)
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Tests for the Phase 5.1 scope vocabulary.

Everything here runs against the REAL tool set, parsed out of
services/*/tools.py by tool_fixtures - no database, no MCP server, no
network. That is the point of keeping api/scopes.py pure: the rules
that decide what an agent may do are testable in milliseconds, so there
is no excuse for not testing them.
"""


REGISTRY = build_registry()
TOOLS = REGISTRY.all()
CATALOG = scope_catalog(TOOLS)


# ---------------------------------------------------------------------
# parse_scope
# ---------------------------------------------------------------------


def test_parse_scope_splits_three_parts():
    assert parse_scope("github:issue:write") == ("github", "issue", "write")


def test_parse_scope_accepts_the_wildcard_resource():
    assert parse_scope("github:*:read") == ("github", "*", "read")


def test_parse_scope_rejects_wrong_shape():
    for bad in ("github", "github:issue", "github:issue:write:extra"):
        try:
            parse_scope(bad)
            raise AssertionError(f"{bad!r} should not parse")
        except InvalidScope:
            pass


def test_parse_scope_rejects_an_unknown_action():
    # "delete" looks perfectly reasonable and no tool will ever require
    # it - build_permissions only emits read / write / admin. Granting
    # it would look like access and be none.
    try:
        parse_scope("github:repository:delete")
        raise AssertionError("unknown action should not parse")
    except InvalidScope:
        pass


def test_parse_scope_rejects_empty_parts():
    try:
        parse_scope("github::write")
        raise AssertionError("empty resource should not parse")
    except InvalidScope:
        pass


# ---------------------------------------------------------------------
# scope_catalog
# ---------------------------------------------------------------------


def test_catalog_is_derived_from_real_tools():
    # Every scope in the catalogue came off a tool, and every tool
    # contributed. Nothing is hand-maintained.
    assert CATALOG
    assert all(":" in scope for scope in CATALOG)

    for tool in TOOLS:
        for scope in tool.permissions:
            assert scope in CATALOG


def test_catalog_contains_both_granularities():
    assert "github:issue:write" in CATALOG
    assert "github:*:write" in CATALOG
    assert "github:*:read" in CATALOG


def test_every_catalog_entry_parses():
    for scope in CATALOG:
        parse_scope(scope)


# ---------------------------------------------------------------------
# validate_scope
# ---------------------------------------------------------------------


def test_validate_accepts_a_real_scope():
    assert validate_scope("github:issue:write", CATALOG) == "github:issue:write"


def test_validate_normalises_case_and_whitespace():
    assert validate_scope("  GitHub:Issue:Write  ", CATALOG) == "github:issue:write"


def test_validate_rejects_a_plausible_typo():
    # THE test in this file. "github:issues:write" (plural) is well
    # formed and matches no tool. Stored, it shows as granted in the UI
    # while granting nothing - a permission bug that fails open in the
    # user's head and closed in the system.
    try:
        validate_scope("github:issues:write", CATALOG)
        raise AssertionError("a typo'd scope must not validate")
    except InvalidScope:
        pass


def test_validate_rejects_an_unknown_service():
    try:
        validate_scope("stripe:charge:write", CATALOG)
        raise AssertionError("an unknown service must not validate")
    except InvalidScope:
        pass


# ---------------------------------------------------------------------
# default_scopes
# ---------------------------------------------------------------------


def test_defaults_are_read_only():
    defaults = default_scopes(TOOLS)

    assert defaults
    assert all(scope.endswith(":read") for scope in defaults)


def test_defaults_never_include_write_or_admin():
    for scope in default_scopes(TOOLS):
        assert ":write" not in scope
        assert ":admin" not in scope


def test_defaults_are_wildcards_one_per_service():
    defaults = default_scopes(TOOLS)

    services = {scope.split(":")[0] for scope in defaults}

    assert len(defaults) == len(services)
    assert all(scope.split(":")[1] == "*" for scope in defaults)


def test_defaults_are_all_grantable():
    # A default the API would refuse to grant is a bootstrapping bug:
    # the agent would ship with a permission the user could never
    # restore after revoking it.
    for scope in default_scopes(TOOLS):
        assert validate_scope(scope, CATALOG) == scope


def test_defaults_cover_every_read_tool_and_no_others():
    defaults = default_scopes(TOOLS)

    permitted = {tool.name for tool in tools_permitted_by(defaults, TOOLS)}

    expected = {tool.name for tool in TOOLS if tool.read_only}

    assert permitted == expected
