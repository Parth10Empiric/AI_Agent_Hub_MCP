from __future__ import annotations

from enum import Enum


"""
The audit vocabulary (Phase 5.8).

WHY THIS MOVED OUT OF api/scopes.py

It started there because the only auditable events were scope grants.
With logins, connections, approvals and executions in the same trail it
is no longer scope vocabulary - and a module that owns two unrelated
vocabularies is one that gets imported for the wrong half.

LOGGING IS FOR YOU. AUDITING IS FOR SOMEONE ELSE, LATER, WHO DOES NOT
TRUST YOU.

That difference decides everything about this table: append-only,
because a log you could have edited answers "what happened?" with
"whatever someone last decided it should say"; identifiers rather than
content, because 12-month retention means anything recorded is
something you are keeping; and one table rather than several, because
the query that matters is asked under pressure by somebody who has
never seen the schema.
"""


class AuditAction(str, Enum):
    """
    Everything worth recording, named consistently.

    `resource.verb`, past tense, always. Consistency is not tidiness
    here: this column is what somebody greps at 2am, and
    "scope.granted" next to "granted_scope" next to "GRANT_SCOPE" means
    every search finds two thirds of the answer.
    """

    def __str__(self) -> str:
        """
        The VALUE, not "AuditAction.SCOPE_GRANTED".

        On Python 3.10 a `class X(str, Enum)` still inherits
        Enum.__str__, so str() produces the member name while every ==
        comparison keeps passing - the wrong string lands in the column
        and nothing else notices. Python 3.11's StrEnum fixes this;
        until then, this does.
        """

        return self.value

    # --- authentication ----------------------------------------------
    #
    # LOGIN_FAILED is the row that matters most on this list. A
    # successful login tells you what happened; a run of failures tells
    # you what someone TRIED.
    USER_REGISTERED = "user.registered"
    LOGIN_SUCCEEDED = "login.succeeded"
    LOGIN_FAILED = "login.failed"
    LOGIN_BLOCKED = "login.blocked"
    LOGOUT = "logout"
    TOKEN_REFRESHED = "token.refreshed"
    TOKEN_REUSE_DETECTED = "token.reuse_detected"

    # --- authorisation -----------------------------------------------
    SCOPE_GRANTED = "scope.granted"
    SCOPE_REVOKED = "scope.revoked"
    TOOL_ENABLED = "tool.enabled"
    TOOL_DISABLED = "tool.disabled"

    # --- agents -------------------------------------------------------
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_ARCHIVED = "agent.archived"

    # --- connections ---------------------------------------------------
    PLUGIN_CONNECTED = "plugin.connected"
    PLUGIN_DISCONNECTED = "plugin.disconnected"
    PLUGIN_REFRESHED = "plugin.refreshed"
    PLUGIN_REFRESH_FAILED = "plugin.refresh_failed"

    # --- approvals -----------------------------------------------------
    #
    # All four, not just the resolutions. "Requested but never
    # answered" is a different story from "denied", and the ratio
    # between them is the health of the whole approval feature.
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_EXPIRED = "approval.expired"

    # --- executions ----------------------------------------------------
    #
    # WRITE, DELETE and ADMIN only. Recording reads as well would
    # multiply the volume by roughly ten to answer a question nobody
    # asks, and on an append-only table with a year's retention,
    # volume is a decision you cannot take back.
    TOOL_EXECUTED = "tool.executed"
    TOOL_DENIED = "tool.denied"

    # --- rate limits ----------------------------------------------------
    LIMIT_REACHED = "limit.reached"

    # --- administration --------------------------------------------------
    #
    # Nothing writes this yet, and it is here on purpose: "staff
    # accessed a user's data" is the first line a security review looks
    # for, and a vocabulary with no word for it produces a system with
    # no way to say it.
    STAFF_ACCESS = "staff.access"


class ResourceType(str, Enum):
    """
    What an event happened TO.

    A real column, not a key inside `metadata`, because this is what
    every query filters on - and a filter on JSON is a filter no index
    can serve.
    """

    def __str__(self) -> str:
        return self.value

    USER = "user"
    AGENT = "agent"
    CONVERSATION = "conversation"
    PLUGIN = "plugin"
    APPROVAL = "approval"
    SESSION = "session"


# Events that MUST commit with the thing they describe, because the
# event IS the state change. A grant whose audit row is missing is a
# lie about permissions.
#
# Everything else is observational and must never be able to fail the
# thing it observed - a login that succeeded and could not be logged
# would be absurd to refuse.
TRANSACTIONAL_ACTIONS = frozenset(
    {
        AuditAction.SCOPE_GRANTED,
        AuditAction.SCOPE_REVOKED,
        AuditAction.TOOL_ENABLED,
        AuditAction.TOOL_DISABLED,
        AuditAction.APPROVAL_APPROVED,
        AuditAction.APPROVAL_DENIED,
        AuditAction.PLUGIN_CONNECTED,
        AuditAction.PLUGIN_DISCONNECTED,
        AuditAction.AGENT_ARCHIVED,
    }
)


__all__ = ["AuditAction", "ResourceType", "TRANSACTIONAL_ACTIONS"]
