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

from api.audit import AuditAction, ResourceType  # noqa: E402
from api.db.models import AuditLog, User  # noqa: E402
from api.services import audit_service  # noqa: E402

"""
The audit trail against real PostgreSQL.

The unit tests prove the ROWS are built correctly. These prove the
table behaves: that a resource timeline is one query, that a trail
survives the thing it describes, and that one tenant cannot read
another's history.
"""


def _database_url() -> str | None:
    try:
        from api.settings import get_settings

        return get_settings().database_url

    except Exception:
        return None


def run_committed(body) -> bool:
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


async def _user(maker, created) -> uuid.UUID:
    async with maker() as db:
        user = User(
            email=f"audit-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="not-a-real-hash",
        )
        db.add(user)
        await db.commit()

        created.append(user.id)

        return user.id


def test_a_resource_timeline_is_one_query():
    """
    The 2am query, and the reason this is ONE table.

    Across two tables it is a UNION with hand-aligned columns, written
    under pressure by somebody who has never seen the schema.
    """

    async def body(maker, created):

        user_id = await _user(maker, created)
        agent_id = uuid.uuid4()

        async with maker() as db:
            for action in (
                AuditAction.AGENT_CREATED,
                AuditAction.SCOPE_GRANTED,
                AuditAction.APPROVAL_REQUESTED,
                AuditAction.APPROVAL_APPROVED,
            ):
                db.add(
                    audit_service.entry(
                        action,
                        user_id=user_id,
                        resource_type=ResourceType.AGENT,
                        resource_id=agent_id,
                    )
                )

            await db.commit()

        async with maker() as db:
            rows, _, _ = await audit_service.list_for_resource(
                db, ResourceType.AGENT, agent_id, user_id=user_id
            )

            actions = [r.action for r in rows]

            # Authentication, authorisation and approvals in one
            # ordered list.
            assert "agent.created" in actions
            assert "scope.granted" in actions
            assert "approval.approved" in actions

    if not run_committed(body):
        _skipped("test_a_resource_timeline_is_one_query")


def test_one_tenant_cannot_read_anothers_history():

    async def body(maker, created):

        alice = await _user(maker, created)
        bob = await _user(maker, created)

        agent_id = uuid.uuid4()

        async with maker() as db:
            db.add(
                audit_service.entry(
                    AuditAction.SCOPE_GRANTED,
                    user_id=alice,
                    resource_type=ResourceType.AGENT,
                    resource_id=agent_id,
                    scope="github:*:write",
                )
            )
            await db.commit()

        async with maker() as db:
            rows, _, _ = await audit_service.list_for_resource(
                db, ResourceType.AGENT, agent_id, user_id=bob
            )

            # An audit trail is exactly the wrong thing to leak across
            # accounts - it is a list of everything somebody did.
            assert rows == []

    if not run_committed(body):
        _skipped("test_one_tenant_cannot_read_anothers_history")


def test_the_trail_outlives_the_thing_it_describes():
    """
    The ids are plain columns, not foreign keys, on purpose.

    A cascading FK would mean deleting an agent erases the evidence of
    what that agent was allowed to do - so deleting the evidence
    becomes a step in the attack.
    """

    async def body(maker, created):

        user_id = await _user(maker, created)
        agent_id = uuid.uuid4()

        async with maker() as db:
            db.add(
                audit_service.entry(
                    AuditAction.AGENT_ARCHIVED,
                    user_id=user_id,
                    resource_type=ResourceType.AGENT,
                    # An id that does not exist in `agents` at all.
                    resource_id=agent_id,
                )
            )

            # No foreign key means no violation, which is the point.
            await db.commit()

        async with maker() as db:
            row = await db.scalar(
                select(AuditLog).where(AuditLog.resource_id == agent_id)
            )

            assert row is not None
            assert row.action == "agent.archived"

    if not run_committed(body):
        _skipped("test_the_trail_outlives_the_thing_it_describes")


def test_one_request_id_ties_a_whole_request_together():

    async def body(maker, created):

        from api.request_context import (
            new_request_id,
            reset_request_context,
            set_request_context,
        )

        user_id = await _user(maker, created)

        request_id = new_request_id()

        tokens = set_request_context(request_id, "8.8.8.8")

        try:
            async with maker() as db:
                for action in (
                    AuditAction.SCOPE_GRANTED,
                    AuditAction.TOOL_ENABLED,
                ):
                    db.add(
                        audit_service.entry(
                            action,
                            user_id=user_id,
                            resource_type=ResourceType.AGENT,
                            resource_id=uuid.uuid4(),
                        )
                    )
                await db.commit()

        finally:
            reset_request_context(tokens)

        async with maker() as db:
            rows = list(
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.request_id == request_id
                    )
                )
            )

            # "It failed at 2:14 and said req_8f3a" hands you the whole
            # request instead of a timestamp to guess from.
            assert len(rows) == 2

    if not run_committed(body):
        _skipped("test_one_request_id_ties_a_whole_request_together")


def test_metadata_holds_identifiers_not_content():
    """
    Append-only plus twelve months of retention means anything written
    here is something you have committed to KEEPING.

    A copy of your users' Slack messages that you have forbidden
    yourself from deleting is not an audit log, it is a liability.
    """

    async def body(maker, created):

        user_id = await _user(maker, created)

        async with maker() as db:
            db.add(
                audit_service.entry(
                    AuditAction.APPROVAL_APPROVED,
                    user_id=user_id,
                    resource_type=ResourceType.AGENT,
                    resource_id=uuid.uuid4(),
                    tool_name="slack_send_message",
                    channel_id="C0123",
                )
            )
            await db.commit()

        async with maker() as db:
            row = await db.scalar(
                select(AuditLog).where(AuditLog.user_id == user_id)
            )

            # Which channel, not what was said.
            assert row.meta["channel_id"] == "C0123"
            assert "text" not in row.meta
            assert "message" not in row.meta

    if not run_committed(body):
        _skipped("test_metadata_holds_identifiers_not_content")
