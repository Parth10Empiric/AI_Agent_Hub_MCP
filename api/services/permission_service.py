from __future__ import annotations

import uuid

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine

from api.audit import AuditAction, ResourceType
from api.db.models import (
    Agent,
    AgentScope,
    AgentTool,
    AuditLog,
    PluginConnection,
)
from api.pagination import Cursor, build_page
from api.schemas.permission import (
    AgentScopes,
    PermissionAuditPage,
    PermissionAuditRead,
    ScopeOption,
    ScopeRead,
)
from api.scopes import InvalidScope, parse_scope, scope_catalog, validate_scope
from api.services import audit_service
from api.services.agent_service import AgentNotFound


"""
Granting, revoking and auditing scopes.

WHY A SEPARATE SERVICE FROM agent_service

Different lifetimes and different blast radius. Renaming an agent is a
preference; granting github:*:write is a security decision that must
leave a permanent record. Keeping them apart means the audit write can
never be "forgotten" by someone adding a field to an unrelated update.

The import goes ONE WAY - this module imports agent_service, never the
reverse - so there is no cycle. agent_service writes its own default
scopes at creation time using the models directly.
"""


# ---------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------


async def _owned_agent(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> Agent:
    """
    Load an agent, scoped to its owner.

    The same pattern as agent_service._owned_agent, and for the same
    reason: the ownership test is in the WHERE clause, not an `if`
    after the fetch. On a permissions endpoint that ordering is the
    difference between "user B cannot grant themselves access to user
    A's agent" and a cross-tenant escalation.
    """

    agent = await session.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.user_id == user_id,
        )
    )

    if agent is None:
        raise AgentNotFound(str(agent_id))

    return agent


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


async def granted_scopes(
    session: AsyncSession,
    agent_id: uuid.UUID,
) -> set[str]:
    """
    The grant set the executor's policy is built from.

    Scalars only - the hot path needs a membership test, not ORM
    objects. This is the query that runs once per agent turn, next to
    the enabled-tools query in chat_service._load_turn_context.

    NO OWNERSHIP CHECK HERE ON PURPOSE: the caller has already proved
    ownership by loading the agent. Repeating it would be a second
    query on the hot path for an answer we already have.
    """

    rows = await session.scalars(
        select(AgentScope.scope).where(AgentScope.agent_id == agent_id)
    )

    return set(rows)


def _options(
    engine: AgentEngine,
    granted: set[str],
    connected: set[str] | None = None,
    services: set[str] | None = None,
) -> list[ScopeOption]:
    """
    Every scope the live registry makes grantable, annotated.

    Derived, not stored. A service added to the MCP server shows up
    here on the next restart with no migration - which is the same
    property that makes scope_catalog() the right validator.

    `connected` is the set of services this USER has an account for.
    Every scope is still returned; the ones over an unconnected service
    are simply marked, so the client can put them out of the way.

    Marked, NOT filtered. Two reasons, and the second is the important
    one:

      A permissions page is a security surface. If the server silently
      dropped scopes, an agent that was granted github:*:write while
      GitHub was connected would keep that grant after a disconnect -
      with no row anywhere in the UI to revoke it. A permission you
      cannot see is a permission you cannot take away.

      Deciding what to hide is a presentation question, and the client
      has the context to answer it (this screen wants them tucked away;
      an audit view wants all of them).

    `services` IS filtered, and it is a different axis entirely.

    `connected` asks "does the USER have an account for this?" - a fact
    about the user, true of every one of their agents at once.
    `services` asks "does THIS AGENT draw tools from it?" - a fact about
    one agent, and the answer is no by default.

    An agent only ever sees tools from a namespace it holds rows for, so
    a scope over a service it was never given is not a permission that
    is merely inert - it is a permission over nothing at all. Offering
    it produced the exact confusion this filter exists to end: the user
    granted "google_drive:*:read", the page showed it granted, and the
    agent still answered that Drive was switched off. The switch they
    needed was "add Google Drive to this agent", one screen back.

    THE ONE THING THIS MUST NOT DO is hide something granted. A scope an
    agent already holds stays listed whatever else is true of it - that
    is how it gets revoked, and it is the same rule `connected` follows
    two paragraphs up. It also carries the legacy case: agents that were
    granted Drive scopes before the Services screen existed still have
    them, and they must be visible to be taken away.
    """

    counts: dict[str, int] = {}

    for tool in engine.get_tools():
        for scope in tool.permissions:
            counts[scope] = counts.get(scope, 0) + 1

    options: list[ScopeOption] = []

    for scope in sorted(counts):

        try:
            service, resource, action = parse_scope(scope)

        except InvalidScope:
            # A tool whose classification produced something malformed.
            # Skip it rather than 500 the whole page - the tool is
            # still unusable, which is the safe direction.
            continue

        # Not on this agent, and not granted to it - so it is a choice
        # that could not take effect. Granted always survives: see the
        # docstring.
        if (
            services is not None
            and service not in services
            and scope not in granted
        ):
            continue

        options.append(
            ScopeOption(
                scope=scope,
                service=service,
                resource=resource,
                action=action,
                tool_count=counts[scope],
                granted=scope in granted,
                connected=connected is None or service in connected,

                # False marks the legacy/edge case the filter above
                # lets through: a live grant over a service this agent
                # no longer has. The UI needs to say WHY a switch is
                # there but useless, and "this agent does not have
                # Google Drive" is a different sentence from "you have
                # not connected Google Drive".
                on_agent=services is None or service in services,
            )
        )

    return options


async def list_scopes(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentScopes:

    await _owned_agent(session, user_id, agent_id)

    rows = list(
        await session.scalars(
            select(AgentScope)
            .where(AgentScope.agent_id == agent_id)
            .order_by(AgentScope.scope)
        )
    )

    granted = {row.scope for row in rows}

    # Which services this USER has an account for. A scope over a
    # service with no credential cannot do anything - the tool is
    # offered, called, and fails at the credential resolver - so the
    # client needs to know which choices are real.
    connected = set(
        await session.scalars(
            select(PluginConnection.plugin_key).where(
                PluginConnection.user_id == user_id
            )
        )
    )

    # Which services this AGENT draws tools from - the namespaces it
    # holds agent_tools rows for. Distinct in SQL rather than a set over
    # every row: an agent with 108 tools has four namespaces, and there
    # is no reason to move the other 104 rows across the wire.
    services = set(
        await session.scalars(
            select(AgentTool.namespace)
            .where(AgentTool.agent_id == agent_id)
            .distinct()
        )
    )

    return AgentScopes(
        granted=[ScopeRead.model_validate(row) for row in rows],
        available=_options(engine, granted, connected, services),
    )


async def list_audit(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> PermissionAuditPage:
    """
    This agent's permission history, newest first.

    Read-only by construction: there is no update or delete function in
    this module, and Phase 6 revokes UPDATE and DELETE on the table at
    the database role level so the guarantee does not depend on nobody
    writing one later.
    """

    await _owned_agent(session, user_id, agent_id)

    stmt = (
        select(AuditLog)
        .where(
            AuditLog.resource_type == str(ResourceType.AGENT),
            AuditLog.resource_id == agent_id,
        )
        .order_by(
            AuditLog.occurred_at.desc(),
            AuditLog.id.desc(),
        )
        # limit + 1: the extra row answers "is there another page?"
        # without a second COUNT(*) query.
        .limit(limit + 1)
    )

    if cursor:
        position = Cursor.decode(cursor)

        # ROW-VALUE comparison, not two ANDed conditions. Two audit
        # rows written in the same transaction share created_at to the
        # microsecond, and `created_at < :t AND id < :i` silently drops
        # every row that ties on the timestamp.
        stmt = stmt.where(
            tuple_(AuditLog.occurred_at, AuditLog.id)
            < (position.created_at, position.row_id)
        )

    rows = list(await session.scalars(stmt))

    page, next_cursor, has_more = build_page(
        rows,
        limit,
        key=lambda row: (row.occurred_at, row.id),
    )

    return PermissionAuditPage(
        items=[PermissionAuditRead.from_log(r) for r in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------


async def grant_scope(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    raw_scope: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> AgentScopes:
    """
    Grant one scope.

    Validated against the LIVE catalogue, so a typo cannot be stored.
    "github:issues:write" (plural) parses perfectly and matches no
    tool - stored, it would show as granted in the UI while granting
    precisely nothing. Failing loudly at the boundary is the whole
    point of validate_scope.

    IDEMPOTENT. Granting a scope twice is a no-op and writes no second
    audit row, because a UI that double-fires a click must not produce
    a history that reads like the user changed their mind twice.
    """

    agent = await _owned_agent(session, user_id, agent_id)

    scope = validate_scope(raw_scope, scope_catalog(engine.get_tools()))

    existing = await session.scalar(
        select(AgentScope).where(
            AgentScope.agent_id == agent_id,
            AgentScope.scope == scope,
        )
    )

    if existing is None:

        session.add(
            AgentScope(
                agent_id=agent.id,
                scope=scope,
                granted_by=actor_user_id or user_id,
            )
        )

        # Same transaction as the grant. If the audit write fails the
        # grant must fail with it - a log that is missing the one event
        # someone is investigating is worse than no log, because it is
        # believed.
        audit_service.record(
            session,
            AuditAction.SCOPE_GRANTED,
            user_id=user_id,
            actor_user_id=actor_user_id or user_id,
            resource_type=ResourceType.AGENT,
            resource_id=agent.id,
            ip_address=ip_address,
            scope=scope,
        )

        await session.flush()

    return await list_scopes(session, engine, user_id, agent_id)


async def revoke_scope(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    raw_scope: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> AgentScopes:
    """
    Revoke one scope.

    The row is DELETED, not flagged. agent_scopes means "what is true
    now"; the history is in permission_audit. That keeps the hot-path
    query - run once per agent turn - a plain index scan with no
    "WHERE revoked_at IS NULL" for anyone to forget.

    Not validated against the catalogue. A scope that is no longer
    offered by any tool must still be revocable, otherwise removing a
    service from the MCP server would strand its grants forever.
    """

    await _owned_agent(session, user_id, agent_id)

    scope = raw_scope.strip().lower()

    result = await session.execute(
        delete(AgentScope).where(
            AgentScope.agent_id == agent_id,
            AgentScope.scope == scope,
        )
    )

    if result.rowcount:
        audit_service.record(
            session,
            AuditAction.SCOPE_REVOKED,
            user_id=user_id,
            actor_user_id=actor_user_id or user_id,
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            ip_address=ip_address,
            scope=scope,
        )

        await session.flush()

    return await list_scopes(session, engine, user_id, agent_id)
