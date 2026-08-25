from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine
from agent.schemas import ToolDefinition

from api.audit import AuditAction, ResourceType
from api.db.models import Agent, AgentScope, AgentTool
from api.approvals import may_auto_approve
from api.scopes import default_scopes
from api.services import audit_service
from api.schemas.agent import (
    AgentCreate,
    AgentDetail,
    AgentSummary,
    AgentToolRead,
    AgentToolWrite,
    AgentUpdate,
)


class AgentError(Exception):
    """Base for agent failures."""


class AgentNotFound(AgentError):
    """
    No such agent FOR THIS USER.

    Deliberately does not distinguish "does not exist" from "belongs to
    someone else". The router turns it into 404 either way, because a
    403 would confirm that an id is real - which is exactly the
    information an attacker enumerating ids is looking for.
    """


class UnknownPlugin(AgentError):
    """A requested service is not in the live registry."""


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


async def _owned_agent(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    with_tools: bool = False,
) -> Agent:
    """
    Load an agent, scoped to its owner.

    The ownership check is in the WHERE clause, not an `if` after the
    fetch. That ordering matters: a query that loads by id and then
    compares owners has already read another user's row into memory,
    and one forgotten `if` turns into an IDOR.

    selectinload is required, not an optimisation. Every relationship
    is lazy="raise_on_sql", so touching agent.tools without asking for
    it raises immediately instead of quietly issuing one query per
    agent.
    """

    stmt = select(Agent).where(
        Agent.id == agent_id,
        Agent.user_id == user_id,
    )

    if with_tools:
        stmt = stmt.options(selectinload(Agent.tools))

    agent = await session.scalar(stmt)

    if agent is None:
        raise AgentNotFound(str(agent_id))

    return agent


def _summary(agent: Agent, tools: list[AgentTool]) -> AgentSummary:
    enabled = [t for t in tools if t.enabled]

    return AgentSummary(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        avatar_url=agent.avatar_url,
        model=agent.model,
        temperature=agent.temperature,
        is_archived=agent.is_archived,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        tool_count=len(enabled),
        namespaces=sorted({t.namespace for t in enabled}),
    )


async def list_agents(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[AgentSummary]:
    """
    This user's agents.

    TWO queries total regardless of how many agents there are - one for
    the agents, one for all their tools - because of selectinload.
    Without it this would be 1 + N.

    The catalogue sync happens here too, in ONE batch for every agent
    the user has, so the tool count on this page agrees with the one on
    each agent's settings page. Two screens disagreeing about how many
    tools an agent has is how the frozen-row bug stayed hidden.
    """

    def load() -> list[Agent]:
        stmt = (
            select(Agent)
            .where(Agent.user_id == user_id)
            .options(selectinload(Agent.tools))
            .order_by(Agent.created_at.desc())
        )

        if not include_archived:
            stmt = stmt.where(Agent.is_archived.is_(False))

        return stmt

    agents = list(await session.scalars(load()))

    # Only re-read when the sync actually inserted something - the
    # common case is a no-op and does not deserve a second round trip.
    if await sync_agent_tools(session, engine, [a.id for a in agents]):
        agents = list(await session.scalars(load()))

    return [_summary(a, list(a.tools)) for a in agents]


def _tool_rows_to_read(
    rows: list[AgentTool],
    registry_tools: dict[str, ToolDefinition],
    granted: set[str] | None = None,
) -> list[AgentToolRead]:
    """
    Merge stored settings with LIVE classification and LIVE permissions.

    A row whose tool the MCP server no longer exposes is reported with
    available=False rather than hidden. Hiding it would make a tool
    silently vanish from the agent's configuration; showing it lets the
    UI say "this tool is no longer available" and lets the user clean
    it up deliberately.

    `granted` adds the same treatment for the OTHER reason a ticked
    tool cannot run: no scope covers it. Without this the tool list was
    the only screen in the product that could show a tool as ready when
    it was not - see AgentToolRead for what that cost.

    None means "do not compute it" - callers that have not loaded the
    grants get the old, permissive default rather than a wrong answer
    derived from an empty set, which would mark every tool unusable.
    """

    out: list[AgentToolRead] = []

    for row in sorted(rows, key=lambda r: r.tool_name):

        definition = registry_tools.get(row.tool_name)

        # Both halves of the real gate. A tool is usable only if it is
        # switched on AND some granted scope covers it, which is
        # exactly what DatabaseScopePolicy checks before a call runs.
        if granted is None or definition is None:
            permitted = True
        else:
            permitted = bool(granted.intersection(definition.permissions))

        out.append(
            AgentToolRead(
                tool_name=row.tool_name,
                namespace=row.namespace,
                enabled=row.enabled,
                requires_approval=row.requires_approval,
                description=definition.description if definition else None,
                operation=definition.operation.value if definition else None,
                risk_level=definition.risk_level.value if definition else None,
                available=definition is not None,
                permitted=permitted,

                # Sorted so the UI offers the same grant every time
                # rather than whichever one a set happened to yield
                # first. Any ONE of them is enough; which one to
                # suggest is the UI's call, and it picks the narrowest
                # ("github:issue:write" over "github:*:write") because
                # the smallest grant that unblocks the user is the
                # right default.
                required_scopes=(
                    sorted(definition.permissions) if definition else []
                ),
            )
        )

    return out


async def get_agent(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentDetail:

    # Ownership FIRST. sync_agent_tools writes, and nothing writes on
    # behalf of an agent this user has not proved they own.
    agent = await _owned_agent(session, user_id, agent_id, with_tools=False)

    await sync_agent_tools(session, engine, [agent.id])

    # Re-read rather than using agent.tools: the sync may have just
    # inserted rows, and a relationship loaded before the INSERT would
    # report the old catalogue - the exact staleness this call fixes.
    rows = list(
        await session.scalars(
            select(AgentTool).where(AgentTool.agent_id == agent.id)
        )
    )

    registry_tools = {t.name: t for t in engine.get_tools()}

    # The grants, so each tool can report whether it would actually
    # run. One extra scalar query per agent detail - cheaper by far
    # than a UI that shows a tool as ready when it is not.
    granted = set(
        await session.scalars(
            select(AgentScope.scope).where(AgentScope.agent_id == agent.id)
        )
    )

    base = _summary(agent, rows)

    return AgentDetail(
        **base.model_dump(),
        system_prompt=agent.system_prompt,
        tools=_tool_rows_to_read(rows, registry_tools, granted),
    )


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------


def _default_rows(
    tools: list[ToolDefinition],
    overrides: dict[str, AgentToolWrite],
) -> list[dict]:
    """
    Build the tool rows for an agent.

    THE DEFAULT IS THE IMPORTANT PART:

        enabled           = True
        requires_approval = tool.requires_approval

    WHY EVERY ROW STARTS ENABLED, AND WHY THAT IS STILL SAFE

    `enabled` used to default to `tool.read_only`, because it was a
    user-facing gate: a grid of checkboxes where writes started unticked
    and you opted in tool by tool. That grid is gone. SCOPES are now the
    only thing a user grants, and they are strictly coarser and no less
    strict - a new agent is seeded with read scopes and nothing else
    (api/scopes.py), so no write tool can run until somebody switches a
    write permission on deliberately.

    Keeping the read_only default underneath that would mean granting
    "Create and change issues on GitHub" still did nothing, because a
    second gate nobody can see any more would be shut. The tool would be
    allowed and disabled at once, and the only symptom would be the
    agent insisting it cannot write - the exact confusion the checkbox
    grid was removed to end.

    So the row opens, and the scope check in DatabaseScopePolicy -
    unchanged, still ANDed with this flag in the executor - is what
    actually decides. The flag stays because the API still accepts it:
    it is now a "hide this specific tool from this agent" lever for
    callers that want one, not the security boundary.
    """

    rows: list[dict] = []

    for tool in tools:

        override = overrides.get(tool.name)

        if override is None:
            enabled = True
            requires_approval = tool.requires_approval

        else:
            enabled = override.enabled

            # None means "use the classified default", NOT False.
            requires_approval = (
                tool.requires_approval
                if override.requires_approval is None
                else override.requires_approval
            )

            # CRITICAL tools always ask, whatever the client sent.
            if not may_auto_approve(tool):
                requires_approval = True

        rows.append(
            {
                "tool_name": tool.name,
                "namespace": tool.namespace or "",
                "enabled": enabled,
                "requires_approval": requires_approval,
            }
        )

    return rows


async def sync_agent_tools(
    session: AsyncSession,
    engine: AgentEngine,
    agent_ids: Sequence[uuid.UUID],
) -> int:
    """
    Give existing agents the tools that shipped after they were made.

    THE BUG THIS FIXES

    `agent_tools` rows are written once, at creation, from whatever the
    MCP server exposed that day. An agent created when GitHub had 18
    tools and Slack had 14 keeps exactly 32 rows forever. GitHub now
    exposes 65 and Slack 43 - and those 76 new tools have no row, so
    they are invisible to that agent no matter what its permissions
    say. The settings page reported "32 of 32 tools available", which
    was true about the rows and false about the product.

    Removing tools always appeared to work, because a row whose tool is
    gone is reported as unavailable. Only the growing direction was
    broken - which is why it looked like a cap.

    WHY A LAZY SYNC RATHER THAN A MIGRATION

    A migration runs once; the catalogue grows every time you add a
    service. This runs on the read paths, is idempotent, and inserts
    nothing on the overwhelmingly common call where the agent is
    already current - one SELECT, no write.

    WHICH TOOLS AN AGENT GETS

    The namespaces it already has rows for. That preserves the choice
    made in the create wizard: an agent given GitHub and Slack picks up
    new GitHub and Slack tools, and never silently acquires Google
    Drive because someone connected it later.

    NOT A PERMISSION CHANGE, SO NOT AUDITED

    A new row is `enabled` and covered by nothing. Whether the agent
    may call it is still decided by its scopes, which this does not
    touch. Adding the row only makes the tool visible to a policy that
    was already going to be asked about it.

    Returns the number of rows inserted, so callers can skip work.
    """

    if not agent_ids:
        return 0

    existing = list(
        await session.execute(
            select(
                AgentTool.agent_id,
                AgentTool.namespace,
                AgentTool.tool_name,
            ).where(AgentTool.agent_id.in_(agent_ids))
        )
    )

    # agent -> what it has, and which services it is configured for.
    have: dict[uuid.UUID, set[str]] = {}
    namespaces: dict[uuid.UUID, set[str]] = {}

    for agent_id, namespace, tool_name in existing:
        have.setdefault(agent_id, set()).add(tool_name)
        namespaces.setdefault(agent_id, set()).add(namespace)

    by_namespace: dict[str, list[ToolDefinition]] = {}

    for tool in engine.get_tools():
        by_namespace.setdefault(tool.namespace or "", []).append(tool)

    added = 0

    for agent_id, agent_namespaces in namespaces.items():

        missing = [
            tool
            for namespace in agent_namespaces
            for tool in by_namespace.get(namespace, [])
            if tool.name not in have.get(agent_id, set())
        ]

        if not missing:
            continue

        # Same defaults a brand-new agent would get, from the same
        # function - so a tool added today is configured identically
        # whether the agent was created before or after it existed.
        for row in _default_rows(missing, {}):
            session.add(AgentTool(agent_id=agent_id, **row))
            added += 1

    if added:
        try:
            await session.commit()
        except IntegrityError:
            # Two requests synced the same agent at once and the unique
            # constraint on (agent_id, tool_name) caught the loser.
            # Nothing is wrong: the rows exist either way. Roll back so
            # the session is usable and report no work done.
            await session.rollback()
            return 0

    return added


def _tools_for_plugins(
    engine: AgentEngine,
    plugins: list[str],
) -> list[ToolDefinition]:

    if not plugins:
        return engine.get_tools()

    known = set(engine.registry.servers())

    unknown = [p for p in plugins if p not in known]

    if unknown:
        raise UnknownPlugin(", ".join(sorted(unknown)))

    selected: list[ToolDefinition] = []

    for plugin in plugins:
        selected.extend(engine.registry.by_namespace(plugin))

    return selected


async def create_agent(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    payload: AgentCreate,
) -> AgentDetail:

    tools = _tools_for_plugins(engine, payload.plugins)

    agent = Agent(
        user_id=user_id,
        name=payload.name,
        description=payload.description,
        avatar_url=payload.avatar_url,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
    )

    session.add(agent)

    # Assigns the primary key so the tool rows below can reference it
    # inside this transaction.
    await session.flush()

    for row in _default_rows(tools, payload.tools):
        session.add(AgentTool(agent_id=agent.id, **row))

    # PHASE 5.1: the coarse grants, seeded closed.
    #
    # One wildcard READ scope per service this agent draws tools from,
    # and nothing else. The agent is immediately useful (it can look at
    # things) and immediately harmless (it cannot change anything),
    # which is the same direction _default_rows already takes for the
    # per-tool checkboxes.
    #
    # A write scope is never seeded. "I clicked Create" is not consent
    # to open issues on somebody's repository.
    for scope in sorted(default_scopes(tools)):
        session.add(
            AgentScope(
                agent_id=agent.id,
                scope=scope,
                granted_by=user_id,
            )
        )

    # The audit trail starts at row one. Without this the history reads
    # "nobody ever granted read access" - true of no agent, and the
    # kind of gap that makes the whole log untrustworthy.
    # Observational, but written in this transaction anyway: the agent
    # and its first audit row are created together, and an agent whose
    # history begins with "scope revoked" and no matching creation
    # reads like tampering.
    session.add(
        audit_service.entry(
            AuditAction.AGENT_CREATED,
            user_id=user_id,
            actor_user_id=user_id,
            resource_type=ResourceType.AGENT,
            resource_id=agent.id,
            name=agent.name,
        )
    )

    await session.flush()

    return await get_agent(session, engine, user_id, agent.id)


async def update_agent(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    payload: AgentUpdate,
) -> AgentDetail:
    """
    Apply a partial update.

    exclude_unset is what makes this a PATCH: only fields the client
    actually sent are applied. Without it, every omitted field would
    arrive as None and overwrite a real value with null.
    """

    agent = await _owned_agent(session, user_id, agent_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)

    await session.flush()

    return await get_agent(session, engine, user_id, agent_id)


async def set_tools(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    tools: dict[str, AgentToolWrite],
    *,
    actor_user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> AgentDetail:
    """
    Replace this agent's tool configuration.

    Only tools that exist in the live registry are written. A request
    naming a tool the MCP server does not expose is ignored for that
    entry rather than rejected outright - the client may simply be
    working from a stale catalogue, and failing the whole request would
    make the UI unusable after any MCP change.
    """

    agent = await _owned_agent(session, user_id, agent_id, with_tools=True)

    registry_tools = {t.name: t for t in engine.get_tools()}

    existing = {row.tool_name: row for row in agent.tools}

    for name, setting in tools.items():

        definition = registry_tools.get(name)

        if definition is None:
            continue

        requires_approval = (
            definition.requires_approval
            if setting.requires_approval is None
            else setting.requires_approval
        )

        # PHASE 5.2: "always allow" is never offered for a CRITICAL
        # tool, and refusing it in the UI is not the same as refusing
        # it. A request that asks for it is silently corrected rather
        # than rejected - the rest of the payload is perfectly valid,
        # and failing the whole call would leave the user unable to
        # configure anything else.
        if not may_auto_approve(definition):
            requires_approval = True

        row = existing.get(name)

        # What the agent could do BEFORE this request. A row that does
        # not exist yet counts as disabled - creating it already
        # enabled is a change worth recording.
        was_enabled = row.enabled if row is not None else False

        if row is None:
            session.add(
                AgentTool(
                    agent_id=agent.id,
                    tool_name=name,
                    namespace=definition.namespace or "",
                    enabled=setting.enabled,
                    requires_approval=requires_approval,
                )
            )

        else:
            row.enabled = setting.enabled
            row.requires_approval = requires_approval

        # PHASE 5.1: audit the CHANGE, not the request.
        #
        # A PUT sends the complete desired set, so most entries in it
        # are unchanged. Logging all of them would bury the one line
        # that matters - "github_merge_pull_request was enabled" -
        # under sixty that say nothing happened.
        if setting.enabled != was_enabled:
            audit_service.record(
                session,
                (
                    AuditAction.TOOL_ENABLED
                    if setting.enabled
                    else AuditAction.TOOL_DISABLED
                ),
                user_id=user_id,
                actor_user_id=actor_user_id or user_id,
                resource_type=ResourceType.AGENT,
                resource_id=agent.id,
                ip_address=ip_address,
                tool_name=name,
            )

    await session.flush()

    return await get_agent(session, engine, user_id, agent_id)


async def archive_agent(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> None:
    """
    Soft delete.

    Conversations and tool_executions reference this agent, and users
    delete things by accident. Archiving keeps the history readable; a
    hard delete would either destroy it or leave dangling references.
    """

    agent = await _owned_agent(session, user_id, agent_id)

    agent.is_archived = True

    # PHASE 5.8: archiving is a state change, so it commits with one.
    # "The agent stopped answering" and "somebody archived it" are the
    # same event, and only one of them was previously recorded.
    audit_service.record(
        session,
        AuditAction.AGENT_ARCHIVED,
        user_id=user_id,
        actor_user_id=user_id,
        resource_type=ResourceType.AGENT,
        resource_id=agent.id,
        name=agent.name,
    )

    await session.flush()


# ---------------------------------------------------------------------
# For the executor (Phase 3.8)
# ---------------------------------------------------------------------


async def enabled_tool_names(
    session: AsyncSession,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> set[str]:
    """
    The set the chat endpoint hands to AgentToolPolicy.

    Returns names only - a scalar query, not whole ORM objects. The
    executor needs a membership test and nothing else.
    """

    await _owned_agent(session, user_id, agent_id)

    rows = await session.scalars(
        select(AgentTool.tool_name).where(
            AgentTool.agent_id == agent_id,
            AgentTool.enabled.is_(True),
        )
    )

    return set(rows)
