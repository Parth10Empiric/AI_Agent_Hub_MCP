from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from api.db.models import AgentTool, AuditLog, User  # noqa: E402
from api.schemas.agent import AgentCreate  # noqa: E402
from api.services import agent_service  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
An agent must pick up tools that shipped after it was created.

THE BUG

`agent_tools` rows were written once, at creation. An agent made when
GitHub exposed 18 tools and Slack 14 kept exactly 32 rows forever, so
the 76 tools added since were invisible to it whatever its permissions
said - and the settings page reported "32 of 32 tools available", which
was true about the table and false about the product.

Removing tools always looked fine, because a row whose tool has gone is
reported as unavailable. Only growth was broken, which is why it
presented as a cap at whatever the catalogue size happened to be on the
day the agent was made.

WHY THIS RUNS AGAINST REAL POSTGRESQL

The thing under test is an INSERT and its unique constraint. A fake
session would assert that the code calls session.add, which is not the
question - the question is whether the rows exist afterwards.
"""


REGISTRY = build_registry()


class FakeEngine:
    def __init__(self) -> None:
        self.registry = REGISTRY

    def get_tools(self):
        return self.registry.all()


ENGINE = FakeEngine()


def _database_url() -> str | None:
    try:
        from api.settings import get_settings

        return get_settings().database_url

    except Exception:
        return None


def run_committed(body) -> bool:
    """
    Run one async body against the real database, then clean up.

    Returns False when the database is unreachable, so the test reports
    a skip rather than a failure. Same contract as the approval tests.
    """

    url = _database_url()

    if not url:
        return False

    async def main() -> bool:
        engine = create_async_engine(url)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with engine.connect():
                pass

        except Exception:
            await engine.dispose()
            return False

        created: list[uuid.UUID] = []

        try:
            await body(maker, created)

        finally:
            async with maker() as db:
                for user_id in created:
                    # Audit rows hold no foreign key to the agent, so
                    # nothing cascades them away.
                    await db.execute(
                        delete(AuditLog).where(AuditLog.user_id == user_id)
                    )
                    await db.execute(delete(User).where(User.id == user_id))

                await db.commit()

            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


async def _agent_with_stale_rows(maker, created, keep_namespaces):
    """
    An agent frozen at an older catalogue.

    Built the honest way round: create it normally, then DELETE most of
    its rows. That is exactly the state a real agent from August is in -
    rows for the tools that existed then, nothing for the rest - and it
    cannot drift from the schema the way a hand-inserted fixture would.
    """

    async with maker() as db:

        user = User(
            email=f"sync-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="not-a-real-hash",
        )
        db.add(user)
        await db.flush()

        created.append(user.id)

        detail = await agent_service.create_agent(
            db,
            ENGINE,
            user.id,
            AgentCreate(name="Stale", system_prompt="test"),
        )

        # Keep three rows per kept namespace and delete everything else.
        keep = [
            tool.name
            for namespace in keep_namespaces
            for tool in sorted(
                (t for t in REGISTRY.all() if t.namespace == namespace),
                key=lambda t: t.name,
            )[:3]
        ]

        await db.execute(
            delete(AgentTool).where(
                AgentTool.agent_id == detail.id,
                AgentTool.tool_name.not_in(keep),
            )
        )

        await db.commit()

        return user.id, detail.id, keep


async def _tool_names(maker, agent_id) -> set[str]:
    async with maker() as db:
        return set(
            await db.scalars(
                select(AgentTool.tool_name).where(
                    AgentTool.agent_id == agent_id
                )
            )
        )


def test_sync_adds_tools_that_shipped_later():

    async def body(maker, created):

        _, agent_id, keep = await _agent_with_stale_rows(
            maker, created, ["github", "slack"]
        )

        before = await _tool_names(maker, agent_id)
        assert before == set(keep)

        async with maker() as db:
            added = await agent_service.sync_agent_tools(
                db, ENGINE, [agent_id]
            )

        assert added > 0

        after = await _tool_names(maker, agent_id)

        # Every GitHub and Slack tool the server exposes today, not the
        # handful that survived. This is the assertion that fails if
        # rows are ever frozen at creation again.
        expected = {
            tool.name
            for tool in REGISTRY.all()
            if tool.namespace in {"github", "slack"}
        }

        assert after == expected
        assert len(after) > len(before)

    if not run_committed(body):
        _skipped("test_sync_adds_tools_that_shipped_later")


def test_sync_does_not_add_services_the_agent_never_had():
    """
    Growth is limited to the services the agent was given.

    Connecting Google Drive next month must not silently hand it to an
    agent someone built for GitHub. The create wizard's choice of
    services is a decision, and a catalogue sync is not permission to
    revisit it.
    """

    async def body(maker, created):

        _, agent_id, _ = await _agent_with_stale_rows(
            maker, created, ["github"]
        )

        async with maker() as db:
            await agent_service.sync_agent_tools(db, ENGINE, [agent_id])

        after = await _tool_names(maker, agent_id)

        namespaces = {
            tool.namespace
            for tool in REGISTRY.all()
            if tool.name in after
        }

        assert namespaces == {"github"}

    if not run_committed(body):
        _skipped("test_sync_does_not_add_services_the_agent_never_had")


def test_sync_is_idempotent():
    """
    The common call does no work.

    This runs on every agent list, every settings load and every chat
    turn. If a current agent still wrote rows, it would be a write on
    the hot path - and the second call proves it is not.
    """

    async def body(maker, created):

        _, agent_id, _ = await _agent_with_stale_rows(
            maker, created, ["github"]
        )

        async with maker() as db:
            first = await agent_service.sync_agent_tools(
                db, ENGINE, [agent_id]
            )

        async with maker() as db:
            second = await agent_service.sync_agent_tools(
                db, ENGINE, [agent_id]
            )

        assert first > 0
        assert second == 0

    if not run_committed(body):
        _skipped("test_sync_is_idempotent")


def test_sync_of_nothing_is_free():
    """No agents, no queries, no crash on an empty IN () clause."""

    async def body(maker, created):
        async with maker() as db:
            assert await agent_service.sync_agent_tools(db, ENGINE, []) == 0

    if not run_committed(body):
        _skipped("test_sync_of_nothing_is_free")
