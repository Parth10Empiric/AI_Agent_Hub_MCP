from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine
from agent.schemas import ToolDefinition

from api.db.models import Agent, AgentTool
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
    user_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[AgentSummary]:
    """
    This user's agents.

    TWO queries total regardless of how many agents there are - one for
    the agents, one for all their tools - because of selectinload.
    Without it this would be 1 + N.
    """

    stmt = (
        select(Agent)
        .where(Agent.user_id == user_id)
        .options(selectinload(Agent.tools))
        .order_by(Agent.created_at.desc())
    )

    if not include_archived:
        stmt = stmt.where(Agent.is_archived.is_(False))

    agents = list(await session.scalars(stmt))

    return [_summary(a, list(a.tools)) for a in agents]


def _tool_rows_to_read(
    rows: list[AgentTool],
    registry_tools: dict[str, ToolDefinition],
) -> list[AgentToolRead]:
    """
    Merge stored settings with LIVE classification.

    A row whose tool the MCP server no longer exposes is reported with
    available=False rather than hidden. Hiding it would make a tool
    silently vanish from the agent's configuration; showing it lets the
    UI say "this tool is no longer available" and lets the user clean
    it up deliberately.
    """

    out: list[AgentToolRead] = []

    for row in sorted(rows, key=lambda r: r.tool_name):

        definition = registry_tools.get(row.tool_name)

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
            )
        )

    return out


async def get_agent(
    session: AsyncSession,
    engine: AgentEngine,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentDetail:

    agent = await _owned_agent(session, user_id, agent_id, with_tools=True)

    rows = list(agent.tools)

    registry_tools = {t.name: t for t in engine.get_tools()}

    base = _summary(agent, rows)

    return AgentDetail(
        **base.model_dump(),
        system_prompt=agent.system_prompt,
        tools=_tool_rows_to_read(rows, registry_tools),
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

        enabled           = tool.read_only
        requires_approval = tool.requires_approval

    Both come from Phase 2 classification, so a new agent can READ
    everything and WRITE nothing until the user deliberately opts in.

    That direction is the correct one for a product holding somebody's
    real GitHub account. The opposite default - everything on, switch
    off what you fear - means the first mistake is destructive rather
    than merely inconvenient.
    """

    rows: list[dict] = []

    for tool in tools:

        override = overrides.get(tool.name)

        if override is None:
            enabled = tool.read_only
            requires_approval = tool.requires_approval

        else:
            enabled = override.enabled

            # None means "use the classified default", NOT False.
            requires_approval = (
                tool.requires_approval
                if override.requires_approval is None
                else override.requires_approval
            )

        rows.append(
            {
                "tool_name": tool.name,
                "namespace": tool.namespace or "",
                "enabled": enabled,
                "requires_approval": requires_approval,
            }
        )

    return rows


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

        row = existing.get(name)

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
