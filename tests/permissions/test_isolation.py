from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from api.db.models import Agent, AgentScope, AuditLog, User  # noqa: E402
from api.schemas.agent import AgentCreate  # noqa: E402
from api.scopes import InvalidScope  # noqa: E402
from api.services import agent_service, permission_service  # noqa: E402
from api.services.agent_service import AgentNotFound  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Cross-tenant isolation for the Phase 5.1 permission model.

Phase5.md says to write these BEFORE building approvals on top of the
data model, and it is right: a cross-tenant leak is the one bug that
ends a client relationship, and it is cheapest to find while nothing
depends on the schema yet.

HOW THESE RUN WITHOUT A THROWAWAY DATABASE

Each test opens a connection, starts a transaction, runs everything
inside it, and ROLLS BACK. The service layer only ever flushes - the
commit lives in api/db/session.get_db - so nothing here can reach the
real database permanently. Deleting the rollback is the only way to
leave residue.

If PostgreSQL is not running the tests report that and pass, rather
than failing for a reason that has nothing to do with the code.
"""


REGISTRY = build_registry()


class FakeEngine:
    """
    Enough AgentEngine for the services under test.

    They call exactly one method - get_tools() - plus registry access
    for plugin filtering. Standing up the real engine would spawn the
    MCP server subprocess and classify 161 tools, which these tests do
    not need: the fixture registry already IS those tools.
    """

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


async def _make_user(session: AsyncSession, label: str) -> User:
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@isolation.test",
        password_hash="not-a-real-hash",
        full_name=label,
    )

    session.add(user)
    await session.flush()

    return user


async def _make_agent(session: AsyncSession, user: User, name: str):
    return await agent_service.create_agent(
        session,
        ENGINE,
        user.id,
        AgentCreate(
            name=name,
            system_prompt="You are a test agent.",
        ),
    )


def run_in_transaction(body) -> bool:
    """
    Run one async test body inside a transaction that is rolled back.

    Returns False when the database is unreachable, so each test can
    report a skip instead of a failure.
    """

    url = _database_url()

    if not url:
        return False

    async def main() -> bool:
        engine = create_async_engine(url)

        try:
            conn = await engine.connect()

        except Exception:
            await engine.dispose()
            return False

        trans = await conn.begin()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        try:
            await body(session)

        finally:
            await session.close()

            # Everything above is discarded. No test data ever lands in
            # the developer's database.
            await trans.rollback()
            await conn.close()
            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


# ---------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------


def test_a_new_agent_is_granted_reads_only():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        detail = await _make_agent(session, user, "Reader")

        scopes = await permission_service.granted_scopes(session, detail.id)

        assert scopes
        assert all(scope.endswith(":read") for scope in scopes)
        assert all(":write" not in scope for scope in scopes)

    if not run_in_transaction(body):
        _skipped("test_a_new_agent_is_granted_reads_only")


def test_agent_creation_is_audited():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        detail = await _make_agent(session, user, "Audited")

        count = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.resource_id == detail.id)
        )

        assert count == 1

    if not run_in_transaction(body):
        _skipped("test_agent_creation_is_audited")


# ---------------------------------------------------------------------
# Isolation - the tests Phase5.md calls mandatory
# ---------------------------------------------------------------------


def test_user_b_cannot_read_user_a_scopes():

    async def body(session: AsyncSession) -> None:

        user_a = await _make_user(session, "a")
        user_b = await _make_user(session, "b")

        agent_a = await _make_agent(session, user_a, "A's agent")

        try:
            await permission_service.list_scopes(
                session, ENGINE, user_b.id, agent_a.id
            )
            raise AssertionError("user B read user A's scopes")

        except AgentNotFound:
            # AgentNotFound, not a Forbidden error. The router turns it
            # into 404, which tells an enumerating attacker nothing
            # about whether the id is real.
            pass

    if not run_in_transaction(body):
        _skipped("test_user_b_cannot_read_user_a_scopes")


def test_user_b_cannot_grant_on_user_a_agent():

    async def body(session: AsyncSession) -> None:

        user_a = await _make_user(session, "a")
        user_b = await _make_user(session, "b")

        agent_a = await _make_agent(session, user_a, "A's agent")

        before = await permission_service.granted_scopes(session, agent_a.id)

        try:
            await permission_service.grant_scope(
                session, ENGINE, user_b.id, agent_a.id, "github:*:write"
            )
            raise AssertionError("user B granted itself write on A's agent")

        except AgentNotFound:
            pass

        after = await permission_service.granted_scopes(session, agent_a.id)

        # The privilege escalation attempt changed nothing at all.
        assert before == after
        assert "github:*:write" not in after

    if not run_in_transaction(body):
        _skipped("test_user_b_cannot_grant_on_user_a_agent")


def test_user_b_cannot_revoke_on_user_a_agent():

    async def body(session: AsyncSession) -> None:

        user_a = await _make_user(session, "a")
        user_b = await _make_user(session, "b")

        agent_a = await _make_agent(session, user_a, "A's agent")

        before = await permission_service.granted_scopes(session, agent_a.id)

        try:
            await permission_service.revoke_scope(
                session, ENGINE, user_b.id, agent_a.id, sorted(before)[0]
            )
            raise AssertionError("user B revoked a scope on A's agent")

        except AgentNotFound:
            pass

        assert await permission_service.granted_scopes(session, agent_a.id) == before

    if not run_in_transaction(body):
        _skipped("test_user_b_cannot_revoke_on_user_a_agent")


def test_user_b_cannot_read_user_a_audit_log():

    async def body(session: AsyncSession) -> None:

        user_a = await _make_user(session, "a")
        user_b = await _make_user(session, "b")

        agent_a = await _make_agent(session, user_a, "A's agent")

        try:
            await permission_service.list_audit(session, user_b.id, agent_a.id)
            raise AssertionError("user B read user A's audit log")

        except AgentNotFound:
            pass

    if not run_in_transaction(body):
        _skipped("test_user_b_cannot_read_user_a_audit_log")


def test_grants_do_not_leak_between_two_agents_of_the_same_user():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")

        one = await _make_agent(session, user, "Writer")
        two = await _make_agent(session, user, "Reader")

        await permission_service.grant_scope(
            session, ENGINE, user.id, one.id, "github:*:write"
        )

        assert "github:*:write" in await permission_service.granted_scopes(
            session, one.id
        )
        assert "github:*:write" not in await permission_service.granted_scopes(
            session, two.id
        )

    if not run_in_transaction(body):
        _skipped("test_grants_do_not_leak_between_two_agents_of_the_same_user")


# ---------------------------------------------------------------------
# Grant / revoke behaviour
# ---------------------------------------------------------------------


def test_grant_is_idempotent_and_writes_one_audit_row():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        agent = await _make_agent(session, user, "Writer")

        for _ in range(3):
            await permission_service.grant_scope(
                session, ENGINE, user.id, agent.id, "github:issue:write"
            )

        rows = await session.scalar(
            select(func.count())
            .select_from(AgentScope)
            .where(
                AgentScope.agent_id == agent.id,
                AgentScope.scope == "github:issue:write",
            )
        )

        audits = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_id == agent.id,
                AuditLog.action == "scope.granted",
            )
        )

        assert rows == 1

        # A double-clicked button must not produce a history that reads
        # like the user changed their mind three times.
        assert audits == 1

    if not run_in_transaction(body):
        _skipped("test_grant_is_idempotent_and_writes_one_audit_row")


def test_revoke_removes_the_grant_and_is_audited():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        agent = await _make_agent(session, user, "Writer")

        await permission_service.grant_scope(
            session, ENGINE, user.id, agent.id, "github:issue:write"
        )
        await permission_service.revoke_scope(
            session, ENGINE, user.id, agent.id, "github:issue:write"
        )

        scopes = await permission_service.granted_scopes(session, agent.id)

        assert "github:issue:write" not in scopes

        actions = list(
            await session.scalars(
                select(AuditLog.action).where(
                    AuditLog.resource_id == agent.id
                )
            )
        )

        # The grant is gone from agent_scopes but BOTH events survive in
        # the audit log. That split - current state in one table,
        # history in another - is the whole design.
        assert "scope.granted" in actions
        assert "scope.revoked" in actions

    if not run_in_transaction(body):
        _skipped("test_revoke_removes_the_grant_and_is_audited")


def test_revoking_a_scope_that_was_never_granted_is_silent():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        agent = await _make_agent(session, user, "Reader")

        await permission_service.revoke_scope(
            session, ENGINE, user.id, agent.id, "github:issue:write"
        )

        revocations = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_id == agent.id,
                AuditLog.action == "scope.revoked",
            )
        )

        # Nothing changed, so nothing is logged. An audit log full of
        # non-events is one nobody reads.
        assert revocations == 0

    if not run_in_transaction(body):
        _skipped("test_revoking_a_scope_that_was_never_granted_is_silent")


def test_a_typo_scope_is_refused():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        agent = await _make_agent(session, user, "Writer")

        try:
            await permission_service.grant_scope(
                session, ENGINE, user.id, agent.id, "github:issues:write"
            )
            raise AssertionError("a typo'd scope was stored")

        except InvalidScope:
            pass

        assert "github:issues:write" not in await permission_service.granted_scopes(
            session, agent.id
        )

    if not run_in_transaction(body):
        _skipped("test_a_typo_scope_is_refused")


# ---------------------------------------------------------------------
# The audit log outlives its subject
# ---------------------------------------------------------------------


def test_deleting_an_agent_removes_its_grants_but_not_its_audit_trail():

    async def body(session: AsyncSession) -> None:

        user = await _make_user(session, "owner")
        agent = await _make_agent(session, user, "Doomed")

        await permission_service.grant_scope(
            session, ENGINE, user.id, agent.id, "github:*:write"
        )

        # A core DELETE, not session.delete(): this is testing the
        # DATABASE's cascade rule, which applies even to raw SQL.
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.flush()

        grants = await session.scalar(
            select(func.count())
            .select_from(AgentScope)
            .where(AgentScope.agent_id == agent.id)
        )

        audits = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.resource_id == agent.id)
        )

        # Grants cascade away - they describe an agent that no longer
        # exists.
        assert grants == 0

        # The audit trail does NOT. If deleting an agent erased the
        # record of what it was allowed to do, deleting the evidence
        # would be a step in the attack.
        assert audits >= 2

    if not run_in_transaction(body):
        _skipped(
            "test_deleting_an_agent_removes_its_grants_but_not_its_audit_trail"
        )
