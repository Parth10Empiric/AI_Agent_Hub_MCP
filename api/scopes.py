from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from agent.schemas import ToolDefinition

# Moved to api/audit.py in Phase 5.8: with logins, connections and
# executions in the same trail it stopped being scope vocabulary.
# Re-exported so existing imports keep working.
from api.audit import AuditAction


"""
Scope vocabulary.

A SCOPE is a coarse grant, written as three colon-separated parts:

    service : resource : action
    github  : issue    : write
    github  : *        : read

Nothing in this module touches the database or the network. It is the
shared vocabulary that the policy, the service layer and the API all
agree on - and being pure makes every rule here testable without a
PostgreSQL instance, which is how the permission model stays honest.

WHY SCOPES AT ALL, WHEN agent_tools ALREADY EXISTS

They answer different questions:

    agent_tools    is this tool switched on?      a UI preference
    agent_scopes   may this agent do writes?      the security envelope

Sixty-one checkboxes is not a permission model - it is a form nobody
reads. A scope lets a user say "this agent may read GitHub" once, and
that sentence keeps meaning the same thing when a new GitHub read tool
ships next month.

The two are checked independently, so neither can quietly widen the
other. See DatabaseScopePolicy in api/policies.py.
"""


# The three actions build_permissions() can emit. Kept here as data
# rather than a comment because the validator below has to reject a
# scope like "github:issue:delete" - which looks reasonable, and which
# no tool will ever require, so granting it would silently do nothing.
SCOPE_ACTIONS = frozenset({"read", "write", "admin"})

WILDCARD = "*"


class InvalidScope(ValueError):
    """A scope string that no tool could ever require."""


def parse_scope(scope: str) -> tuple[str, str, str]:
    """
    Split "github:issue:write" into its three parts.

    Raises rather than returning None because every caller here treats
    an unparseable scope as a client error, and an ignored None is how
    a bad scope ends up stored.
    """

    parts = scope.split(":")

    if len(parts) != 3:
        raise InvalidScope(
            f"{scope!r} is not in service:resource:action form."
        )

    service, resource, action = (p.strip() for p in parts)

    if not service or not resource or not action:
        raise InvalidScope(f"{scope!r} has an empty part.")

    if action not in SCOPE_ACTIONS:
        raise InvalidScope(
            f"{scope!r} has action {action!r}; expected one of "
            f"{sorted(SCOPE_ACTIONS)}."
        )

    return service, resource, action


def scope_catalog(tools: Iterable[ToolDefinition]) -> frozenset[str]:
    """
    Every scope any live tool actually requires.

    Built from ToolDefinition.permissions, which Phase 2 already filled
    in via build_permissions() at discovery time:

        github_create_issue -> ("github:issue:write", "github:*:write")

    So the catalogue is derived, never hand-maintained. Add a tool to
    the MCP server and its scopes appear here on the next restart; no
    migration, no constant to update.

    This is what the API validates a grant against. Storing a scope no
    tool requires is worse than useless: the user sees a grant in the
    UI, believes the agent can act, and it silently cannot.
    """

    return frozenset(
        scope
        for tool in tools
        for scope in tool.permissions
    )


def default_scopes(tools: Iterable[ToolDefinition]) -> set[str]:
    """
    What a brand-new agent is granted: reads, nothing else.

    One wildcard read scope per service the agent draws tools from:

        {"github:*:read", "slack:*:read"}

    This matches the default already used for agent_tools - enabled =
    tool.read_only (see agent_service._default_rows). Both layers start
    closed in the same direction, so a new agent is useful immediately
    and harmless immediately.

    Writes are never defaulted. A write grant is a deliberate sentence
    a human says out loud, and "I clicked create" is not that sentence.
    """

    return {
        f"{tool.namespace}:{WILDCARD}:read"
        for tool in tools
        if tool.read_only and tool.namespace
    }


def validate_scope(scope: str, catalog: frozenset[str]) -> str:
    """
    Normalise and check one scope a client asked to grant.

    Two gates, in order:

        1. is it well formed?          -> parse_scope
        2. does any real tool want it? -> the catalogue

    Gate 2 is the one that catches typos ("github:issues:write", plural)
    which gate 1 happily accepts. A typo'd grant is the worst kind of
    permission bug because it fails OPEN in the user's mind - they think
    access was given - while failing CLOSED in the system.
    """

    normalised = scope.strip().lower()

    parse_scope(normalised)

    if normalised not in catalog:
        raise InvalidScope(
            f"{normalised!r} is not required by any available tool."
        )

    return normalised


def tools_permitted_by(
    granted: frozenset[str] | set[str],
    tools: Iterable[ToolDefinition],
) -> list[ToolDefinition]:
    """
    Filter a tool list down to what these scopes cover.

    Used to decide which tools are worth OFFERING to the model. It is
    not the security check - that happens inside the executor, on the
    tool the model actually asked for. Offering a tool the agent cannot
    use only wastes a round and confuses the answer.
    """

    return [
        tool
        for tool in tools
        if granted.intersection(tool.permissions)
    ]


__all__ = [
    "SCOPE_ACTIONS",
    "WILDCARD",
    "AuditAction",
    "InvalidScope",
    "default_scopes",
    "parse_scope",
    "scope_catalog",
    "tools_permitted_by",
    "validate_scope",
]
