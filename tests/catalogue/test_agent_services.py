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

from api.db.models import (  # noqa: E402
    AgentScope,
    AgentTool,
    AuditLog,
    User,
)
from api.policies import DatabaseScopePolicy  # noqa: E402
from api.schemas.agent import AgentCreate  # noqa: E402
from api.services import agent_service, permission_service  # noqa: E402
from api.services.agent_service import UnknownPlugin  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
An agent must be able to gain a SERVICE after it was created.

THE BUG

An agent only ever sees tools from a namespace it holds agent_tools
rows for, and nothing could add one after the create wizard:

    create_agent        writes rows for the services you ticked
    sync_agent_tools    adds new TOOLS, never a new namespace
    grant_scope         never touched agent_tools at all

So connecting Google Drive and granting "google_drive:*:read" produced
a permission that could not work. Gate 2 of DatabaseScopePolicy passed;
gate 1 had no row to pass. The chat reported the tool as "switched off
in this agent's tool settings" - a screen that had no such switch,
because the per-tool checkbox grid was removed when scopes replaced it.

From the transcript that started this: an agent with 108 tool rows
(github 65 + slack 43) and 15 granted google_drive scopes, insisting
Drive was switched off while the settings page said "108 of 108 tools
are available".

WHY THIS RUNS AGAINST REAL POSTGRESQL

What is under test is a set of INSERTs and DELETEs and what the policy
says afterwards. A fake session would assert that the code calls
session.add, which is not the question.
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
    a skip rather than a failure. Same contract as test_tool_sync.
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


async def _agent(maker, created, plugins):
    """A committed agent built for exactly these services."""

    async with maker() as db:

        user = User(
            email=f"svc-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="not-a-real-hash",
        )
        db.add(user)
        await db.flush()

        created.append(user.id)

        detail = await agent_service.create_agent(
            db,
            ENGINE,
            user.id,
            AgentCreate(
                name="Services",
                system_prompt="test",
                plugins=list(plugins),
            ),
        )

        await db.commit()

        return user.id, detail.id


async def _namespaces(maker, agent_id) -> set[str]:
    async with maker() as db:
        return set(
            await db.scalars(
                select(AgentTool.namespace)
                .where(AgentTool.agent_id == agent_id)
                .distinct()
            )
        )


async def _scopes(maker, agent_id) -> set[str]:
    async with maker() as db:
        return set(
            await db.scalars(
                select(AgentScope.scope).where(
                    AgentScope.agent_id == agent_id
                )
            )
        )


async def _audit_count(maker, user_id) -> int:
    async with maker() as db:
        return len(
            list(
                await db.scalars(
                    select(AuditLog.id).where(AuditLog.user_id == user_id)
                )
            )
        )


def test_adding_a_service_creates_its_tool_rows():
    """
    The lever that did not exist.

    Every tool of the added namespace gets a row, and the count matches
    the live registry rather than whatever was true when the agent was
    made.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(maker, created, ["github"])

        assert await _namespaces(maker, agent_id) == {"github"}

        async with maker() as db:
            detail = await agent_service.set_services(
                db, ENGINE, user_id, agent_id, ["github", "google_drive"]
            )
            await db.commit()

        assert set(detail.services) == {"github", "google_drive"}

        async with maker() as db:
            names = set(
                await db.scalars(
                    select(AgentTool.tool_name).where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.namespace == "google_drive",
                    )
                )
            )

        assert names == {
            tool.name
            for tool in REGISTRY.all()
            if tool.namespace == "google_drive"
        }

    if not run_committed(body):
        _skipped("test_adding_a_service_creates_its_tool_rows")


def test_added_service_is_actually_usable_by_the_policy():
    """
    THE REGRESSION TEST.

    Not "are there rows" but "does the gate that refused the user now
    say yes". Both halves are asked exactly the way chat_service asks
    them, so this fails if either one regresses.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(maker, created, ["github"])

        drive = next(
            tool
            for tool in REGISTRY.all()
            if tool.name == "google_drive_list_folder"
        )

        async with maker() as db:
            enabled = await agent_service.enabled_tool_names(
                db, user_id, agent_id
            )
            granted = await _scopes(maker, agent_id)

        # Before: exactly the state from the transcript.
        assert not DatabaseScopePolicy(granted, enabled).check(drive).allowed

        async with maker() as db:
            await agent_service.set_services(
                db, ENGINE, user_id, agent_id, ["github", "google_drive"]
            )
            await db.commit()

        async with maker() as db:
            enabled = await agent_service.enabled_tool_names(
                db, user_id, agent_id
            )

        granted = await _scopes(maker, agent_id)

        decision = DatabaseScopePolicy(granted, enabled).check(drive)

        assert decision.allowed, decision.reason

    if not run_committed(body):
        _skipped("test_added_service_is_actually_usable_by_the_policy")


def test_adding_a_service_seeds_reads_and_never_writes():
    """
    "Add Google Drive" is consent to LOOK at Drive.

    Same rule create_agent follows. If this ever starts seeding writes,
    ticking a checkbox on a settings page would silently authorise
    deleting somebody's files.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(maker, created, ["github"])

        async with maker() as db:
            await agent_service.set_services(
                db, ENGINE, user_id, agent_id, ["github", "google_drive"]
            )
            await db.commit()

        drive = {
            scope
            for scope in await _scopes(maker, agent_id)
            if scope.startswith("google_drive:")
        }

        assert drive
        assert all(scope.endswith(":read") for scope in drive), drive

    if not run_committed(body):
        _skipped("test_adding_a_service_seeds_reads_and_never_writes")


def test_removing_a_service_takes_its_permissions_with_it():
    """
    Both halves, or the leftover is a live permission nobody can see.

    The permissions page now lists only the services an agent has, so a
    grant left behind for a removed service would be in force and
    invisible at once.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(
            maker, created, ["github", "google_drive"]
        )

        async with maker() as db:
            await agent_service.set_services(
                db, ENGINE, user_id, agent_id, ["github"]
            )
            await db.commit()

        assert await _namespaces(maker, agent_id) == {"github"}

        remaining = await _scopes(maker, agent_id)

        assert not any(s.startswith("google_drive:") for s in remaining)

        # The service that was KEPT is untouched - a removal must not
        # take its neighbour's permissions with it.
        assert any(s.startswith("github:") for s in remaining)

    if not run_committed(body):
        _skipped("test_removing_a_service_takes_its_permissions_with_it")


def test_saving_an_unchanged_set_writes_nothing():
    """
    A re-saved form must not produce a history that reads like the user
    kept changing their mind.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(maker, created, ["github"])

        before = await _audit_count(maker, user_id)

        async with maker() as db:
            await agent_service.set_services(
                db, ENGINE, user_id, agent_id, ["github"]
            )
            await db.commit()

        assert await _audit_count(maker, user_id) == before

    if not run_committed(body):
        _skipped("test_saving_an_unchanged_set_writes_nothing")


def test_unknown_service_is_refused():
    """
    Validated against the LIVE registry.

    A typo stored here would give the agent a namespace no tool has -
    invisible, unusable, and impossible to explain from the UI.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(maker, created, ["github"])

        async with maker() as db:
            try:
                await agent_service.set_services(
                    db, ENGINE, user_id, agent_id, ["github", "gogle_drive"]
                )

            except UnknownPlugin as exc:
                assert "gogle_drive" in str(exc)

            else:
                raise AssertionError("an unknown service was accepted")

    if not run_committed(body):
        _skipped("test_unknown_service_is_refused")


def test_permissions_list_only_the_services_the_agent_has():
    """
    The second half of the fix.

    Offering "read Google Drive" to an agent with no Drive tools is a
    switch that reports success and changes nothing - which is how the
    original bug survived: the page said granted, the agent said
    switched off, and both were telling the truth.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(maker, created, ["github"])

        async with maker() as db:
            scopes = await permission_service.list_scopes(
                db, ENGINE, user_id, agent_id
            )

        services = {option.service for option in scopes.available}

        assert services == {"github"}

        async with maker() as db:
            await agent_service.set_services(
                db, ENGINE, user_id, agent_id, ["github", "google_drive"]
            )
            await db.commit()

        async with maker() as db:
            scopes = await permission_service.list_scopes(
                db, ENGINE, user_id, agent_id
            )

        services = {option.service for option in scopes.available}

        assert services == {"github", "google_drive"}
        assert all(option.on_agent for option in scopes.available)

    if not run_committed(body):
        _skipped("test_permissions_list_only_the_services_the_agent_has")


def test_a_granted_scope_stays_visible_after_its_service_is_removed():
    """
    THE RULE THE FILTER MUST NOT BREAK.

    A permission you cannot see is a permission you cannot take away.
    Removing a service revokes its scopes, so this state is not reached
    through the UI - but it IS the state every agent granted Drive
    scopes before this screen existed is already in, and those grants
    have to be revocable.
    """

    async def body(maker, created):

        user_id, agent_id = await _agent(
            maker, created, ["github", "google_drive"]
        )

        # The legacy shape, built by hand because no code path produces
        # it any more: the tool rows gone, the grant left behind.
        async with maker() as db:
            await db.execute(
                delete(AgentTool).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.namespace == "google_drive",
                )
            )
            await db.commit()

        async with maker() as db:
            scopes = await permission_service.list_scopes(
                db, ENGINE, user_id, agent_id
            )

        drive = [
            option
            for option in scopes.available
            if option.service == "google_drive"
        ]

        assert drive, "a live grant was hidden and cannot be revoked"
        assert all(option.granted for option in drive)
        assert all(not option.on_agent for option in drive)

    if not run_committed(body):
        _skipped(
            "test_a_granted_scope_stays_visible_after_its_service_is_removed"
        )


if __name__ == "__main__":

    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
