from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models.agent import Agent
from api.db.models.execution import ToolExecution
from api.pagination import StringCursor, build_page
from api.schemas.execution import (
    ExecutionListItem,
    ExecutionPage,
    ExecutionStats,
)


# The three real statuses. Anything else is a typo in a query string,
# and rejecting it beats silently returning an empty list that looks
# like "you have no failures".
VALID_STATUSES = frozenset({"success", "failed", "denied"})


class InvalidFilter(Exception):
    """A filter value the API will not accept."""


def _window_start(days: int) -> datetime:
    """
    The oldest timestamp still inside the window.

    timezone.utc explicitly. started_at is stored as TIMESTAMPTZ, and
    comparing it against a naive datetime raises in asyncpg rather than
    guessing a timezone - which is the correct behaviour and a
    confusing error the first time you meet it.
    """

    return datetime.now(timezone.utc) - timedelta(days=days)


async def list_executions(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int = 50,
    cursor: str | None = None,
    status: str | None = None,
    agent_id: uuid.UUID | None = None,
    days: int | None = None,
) -> ExecutionPage:
    """
    One page of this user's tool activity, newest first.

    WHY THE user_id FILTER IS NOT OPTIONAL

    Every row in tool_executions belongs to exactly one user, and this
    query is scoped to the caller in the WHERE clause - not filtered
    afterwards in Python, and not left to the caller to remember. An
    execution id in a URL should never be able to reach another user's
    row.

    ON `denied`

    denied is not a failure. It means the permission policy or the
    approval handler refused a call, which is the system working
    exactly as designed. It gets its own filter value and its own
    colour in the UI for that reason - folding it into `failed` would
    make your own dashboard report broken software when nothing broke.
    """

    if status is not None and status not in VALID_STATUSES:
        raise InvalidFilter(
            f"status must be one of {sorted(VALID_STATUSES)}."
        )

    stmt = (
        # An OUTER join: agent_id is nullable, and an execution whose
        # agent was hard-deleted must still appear. An inner join would
        # silently drop those rows and the page would quietly lie about
        # how much activity there was.
        select(ToolExecution, Agent.name)
        .outerjoin(Agent, Agent.id == ToolExecution.agent_id)
        .where(ToolExecution.user_id == user_id)
        .order_by(
            ToolExecution.started_at.desc(),
            ToolExecution.id.desc(),
        )
        # limit + 1 answers "is there more?" without a second COUNT(*)
        # over the whole table.
        .limit(limit + 1)
    )

    if status:
        stmt = stmt.where(ToolExecution.status == status)

    if agent_id:
        stmt = stmt.where(ToolExecution.agent_id == agent_id)

    if days:
        stmt = stmt.where(ToolExecution.started_at >= _window_start(days))

    if cursor:
        position = StringCursor.decode(cursor)

        # Row-value comparison, matching conversation_service. Two
        # ANDed conditions would drop every row sharing a timestamp
        # with the cursor row but sorting after it on id.
        stmt = stmt.where(
            tuple_(ToolExecution.started_at, ToolExecution.id)
            < (position.created_at, position.row_id)
        )

    rows = (await session.execute(stmt)).all()

    items = [
        ExecutionListItem(
            **{
                column: getattr(execution, column)
                for column in (
                    "id",
                    "tool_name",
                    "namespace",
                    "operation",
                    "risk_level",
                    "status",
                    "started_at",
                    "duration_ms",
                    "attempts",
                    "approved_by_user",
                    "error",
                    "agent_id",
                    "conversation_id",
                )
            },
            agent_name=agent_name,
        )
        for execution, agent_name in rows
    ]

    page, next_cursor, has_more = build_page(
        items,
        limit,
        key=lambda item: (item.started_at, item.id),
        cursor_cls=StringCursor,
    )

    return ExecutionPage(
        items=page,
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_stats(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int = 7,
) -> ExecutionStats:
    """
    Aggregate counts for the dashboard.

    Two GROUP BY queries, not a scan in Python. The whole point of
    writing every ExecutionRecord to the database in Phase 3.8 is that
    questions like this become cheap.
    """

    since = _window_start(days)

    base = (
        select(
            ToolExecution.status,
            func.count().label("n"),
            func.avg(ToolExecution.duration_ms).label("avg_ms"),
        )
        .where(
            ToolExecution.user_id == user_id,
            ToolExecution.started_at >= since,
        )
        .group_by(ToolExecution.status)
    )

    stats = ExecutionStats(window_days=days)

    total_ms = 0.0

    for status, count, avg_ms in (await session.execute(base)).all():
        stats.total += count

        if status in VALID_STATUSES:
            setattr(stats, status, count)

        # A weighted mean. Averaging the per-status averages would
        # weight a single denied call as heavily as 400 successes.
        total_ms += (avg_ms or 0.0) * count

    if stats.total:
        stats.avg_duration_ms = round(total_ms / stats.total, 2)

    by_tool = (
        select(ToolExecution.tool_name, func.count().label("n"))
        .where(
            ToolExecution.user_id == user_id,
            ToolExecution.started_at >= since,
        )
        .group_by(ToolExecution.tool_name)
        .order_by(func.count().desc())
        .limit(10)
    )

    stats.by_tool = {
        name: count for name, count in (await session.execute(by_tool)).all()
    }

    return stats
