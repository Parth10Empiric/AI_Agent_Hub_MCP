from __future__ import annotations

from typing import Any

from .schemas import Operation, RiskLevel
from .text import singularize, split_identifier

"""
Tool classification (Phase 2.2).

Answers three questions about every discovered tool:

    1. What does it do?        -> Operation
    2. How dangerous is it?    -> RiskLevel
    3. What may call it?       -> permission scopes

These answers are what Phase 5 (permissions + human approval) will be
built on, and what stops the router from offering `delete_file` when
the user said "show me my files".

The resolution order below is the important part of this module.
"""


# ---------------------------------------------------------------------
# Verb tables
# ---------------------------------------------------------------------
#
# Applied to the FIRST word after the namespace prefix:
#
#   github_create_issue        -> "create"
#   google_drive_search_files  -> "search"

READ_VERBS = frozenset({
    "get", "list", "search", "read", "fetch", "download", "find",
    "show", "describe", "view", "query", "count", "check", "export",
})

WRITE_VERBS = frozenset({
    "create", "add", "update", "set", "send", "post", "upload",
    "move", "rename", "edit", "patch", "put", "append", "insert",
    "comment", "reply", "schedule", "assign", "close", "merge",
})

DELETE_VERBS = frozenset({
    "delete", "remove", "cancel", "purge", "archive", "clear",
    "revoke", "drop",
})


# ---------------------------------------------------------------------
# Explicit overrides
# ---------------------------------------------------------------------
#
# Two kinds of tool need to be corrected by hand.
#
# (a) Tools whose name does not start with a verb at all. Of your 61
#     tools, exactly four are like this:
#
#         slack_auth_info            -> "auth"
#         slack_channel_history      -> "channel"
#         slack_thread_replies       -> "thread"
#         google_calendar_freebusy   -> "freebusy"
#
#     All four are harmless reads, but the heuristic cannot know that,
#     and the fail-closed default (see below) would classify them as
#     writes and force a pointless approval prompt on every call.
#
# (b) Tools where the verb is right but the danger is wrong. This is
#     the case that a naive verb-based system gets silently,
#     dangerously wrong:
#
#         google_drive_create_file        creates a file you own
#         google_drive_create_permission  hands your file to a stranger
#
#     Both are "create". Only one is a data-exposure event.

OPERATION_OVERRIDES: dict[str, Operation] = {
    # Verb-less reads.
    "slack_auth_info": Operation.READ,
    "slack_channel_history": Operation.READ,
    "slack_thread_replies": Operation.READ,
    "google_calendar_freebusy": Operation.READ,

    # Access-control changes are ADMIN, not plain WRITE/DELETE.
    "google_drive_create_permission": Operation.ADMIN,
    "google_calendar_create_acl_rule": Operation.ADMIN,
    "google_calendar_update_acl_rule": Operation.ADMIN,
    "google_calendar_delete_acl_rule": Operation.ADMIN,
}

RISK_OVERRIDES: dict[str, RiskLevel] = {
    # Irreversible and visible to other humans. You cannot un-send a
    # Slack message to a client, which makes this riskier than most
    # deletes you could simply recreate.
    "slack_send_message": RiskLevel.HIGH,

    # Data exposure: these give other people access to your content.
    "google_drive_create_permission": RiskLevel.HIGH,
    "google_calendar_create_acl_rule": RiskLevel.HIGH,
    "google_calendar_update_acl_rule": RiskLevel.HIGH,
    "google_calendar_delete_acl_rule": RiskLevel.CRITICAL,

    # Destroys user content with no undo through the API.
    "google_drive_delete_file": RiskLevel.CRITICAL,

    # Reads that move data out of the account or reveal who can see it.
    "google_drive_download_file": RiskLevel.LOW,
    "google_drive_list_permissions": RiskLevel.LOW,
    "google_calendar_list_acl": RiskLevel.LOW,
}


# Default risk per operation, used when there is no explicit override.
DEFAULT_RISK: dict[Operation, RiskLevel] = {
    Operation.READ: RiskLevel.SAFE,
    Operation.WRITE: RiskLevel.MEDIUM,
    Operation.DELETE: RiskLevel.HIGH,
    Operation.ADMIN: RiskLevel.HIGH,
}


# ---------------------------------------------------------------------
# Annotation reading
# ---------------------------------------------------------------------


def _operation_from_annotations(
    annotations: dict[str, Any],
) -> Operation | None:
    """
    Read the MCP standard tool hints, if the server sent any.

    The MCP spec defines `readOnlyHint`, `destructiveHint`,
    `idempotentHint` and `openWorldHint`. When a server provides them
    they are authoritative: the server author knows what their tool
    does far better than any name-based guess.

    Your current server does not emit these yet (see the note at the
    bottom of this file), so in practice this returns None today and
    the heuristic takes over. Wiring the path now means that the day
    you add `annotations={"readOnlyHint": True}` to a tool, or connect
    a third-party MCP server that already sets them, classification
    improves with zero code changes.
    """

    if not annotations:
        return None

    def read_hint(*keys: str) -> bool | None:
        for key in keys:
            value = annotations.get(key)
            if isinstance(value, bool):
                return value
        return None

    destructive = read_hint("destructiveHint", "destructive_hint")

    if destructive is True:
        return Operation.DELETE

    read_only = read_hint("readOnlyHint", "read_only_hint")

    if read_only is True:
        return Operation.READ

    if read_only is False:
        return Operation.WRITE

    return None


# ---------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------


def split_tool_name(
    tool_name: str,
    namespace: str | None,
) -> tuple[str | None, str | None]:
    """
    Split a tool name into (verb, resource).

        github_create_issue        -> ("create", "issue")
        google_drive_search_files  -> ("search", "file")
        slack_channel_history      -> (None, "channel history")

    The resource is what the permission scope is built from, so it is
    singularized: a grant should read `github:issue:write`, not
    `github:issues:write`, or you end up with two scopes meaning the
    same thing.
    """

    words = split_identifier(tool_name)

    if namespace:
        namespace_words = split_identifier(namespace)

        if words[: len(namespace_words)] == namespace_words:
            words = words[len(namespace_words):]

    if not words:
        return None, None

    verb = words[0]

    known_verb = (
        verb in READ_VERBS
        or verb in WRITE_VERBS
        or verb in DELETE_VERBS
    )

    if not known_verb:
        # No recognizable verb: the whole remainder is the resource.
        return None, " ".join(words)

    resource_words = words[1:]

    if not resource_words:
        return verb, None

    resource = " ".join(
        singularize(word)
        for word in resource_words
    )

    return verb, resource


def _operation_from_verb(verb: str | None) -> Operation | None:
    if verb is None:
        return None

    if verb in READ_VERBS:
        return Operation.READ

    if verb in DELETE_VERBS:
        return Operation.DELETE

    if verb in WRITE_VERBS:
        return Operation.WRITE

    return None


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------


def classify_operation(
    tool_name: str,
    namespace: str | None,
    annotations: dict[str, Any],
) -> tuple[Operation, str]:
    """
    Decide what a tool does. Returns (operation, source).

    Resolution order, strongest evidence first:

        1. Explicit override      we have inspected this tool by hand
        2. MCP annotations        the server author told us
        3. Verb heuristic         convention in the tool's name
        4. Fail-closed default    we genuinely do not know

    On step 4, the default is WRITE and not READ. That asymmetry is the
    single most important decision in this file, so it is worth being
    explicit about why:

        Guessing READ on a tool that actually writes means the agent
        silently deletes a repository with no approval prompt.

        Guessing WRITE on a tool that actually reads means the user
        sees one unnecessary "Approve?" dialog.

    Those two mistakes are not remotely equal in cost, so when we are
    uncertain we take the harmless one. This is what "fail closed"
    means, and it is the difference between a demo and something you
    can put in front of a client.

    The override table then exists to buy back the few false positives
    this creates — which for your 61 tools is exactly four.
    """

    override = OPERATION_OVERRIDES.get(tool_name)

    if override is not None:
        return override, "override"

    from_annotations = _operation_from_annotations(annotations)

    if from_annotations is not None:
        return from_annotations, "annotation"

    verb, _ = split_tool_name(tool_name, namespace)

    from_verb = _operation_from_verb(verb)

    if from_verb is not None:
        return from_verb, "heuristic"

    return Operation.WRITE, "default"


def classify_risk(
    tool_name: str,
    operation: Operation,
) -> RiskLevel:
    """
    Decide how dangerous a tool is.

    Override first, then the per-operation default. Risk is never
    inferred from the verb alone — see the docstring on `RiskLevel`.
    """

    override = RISK_OVERRIDES.get(tool_name)

    if override is not None:
        return override

    return DEFAULT_RISK[operation]


def build_permissions(
    namespace: str | None,
    resource: str | None,
    operation: Operation,
) -> tuple[str, ...]:
    """
    Build the permission scopes a tool requires.

    Two scopes are emitted per tool:

        github:issue:write     specific — "may write issues"
        github:*:write         wildcard — "may write anything on GitHub"

    Emitting both now means Phase 5 can support coarse grants ("this
    agent may do anything read-only on Drive") and fine grants ("this
    agent may create issues but not close them") with the same check:
    does the agent hold ANY of the tool's scopes?

    Getting this shape right now is cheap. Retrofitting wildcards after
    you have stored per-tool grants in the database is not.
    """

    service = namespace or "unknown"
    target = (resource or "unknown").replace(" ", "_")

    if operation is Operation.ADMIN:
        action = "admin"
    elif operation is Operation.READ:
        action = "read"
    else:
        action = "write"

    return (
        f"{service}:{target}:{action}",
        f"{service}:*:{action}",
    )


# ---------------------------------------------------------------------
# Note for the MCP server side
# ---------------------------------------------------------------------
#
# Everything above is a *client-side* best effort. The properly correct
# fix is for the MCP server to declare this itself, e.g.
#
#     @mcp.tool(
#         annotations={
#             "readOnlyHint": False,
#             "destructiveHint": True,
#         },
#     )
#     def google_drive_delete_file(...): ...
#
# When you get to hardening the server, adding those hints to the ~15
# mutating tools will make `_operation_from_annotations` fire and the
# heuristic become a fallback rather than the primary path. The client
# code does not need to change at all when that happens — which is the
# whole reason the annotation branch exists today.
