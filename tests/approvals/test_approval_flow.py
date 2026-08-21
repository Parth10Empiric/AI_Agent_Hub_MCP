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

from api.approvals import ApprovalStatus, DeferredApproval, WebApproval  # noqa: E402
from api.db.models import (  # noqa: E402
    AgentScope,
    PendingApproval,
    AuditLog,
    User,
)
from api.notifier import ApprovalNotifier  # noqa: E402
from api.schemas.agent import AgentCreate, AgentToolWrite  # noqa: E402
from api.schemas.conversation import ConversationCreate  # noqa: E402
from api.services import (  # noqa: E402
    agent_service,
    approval_service,
    conversation_service,
)
from api.services.approval_service import (  # noqa: E402
    ApprovalAlreadyResolved,
    ApprovalNotFound,
)
from tests.tool_fixtures import build_registry  # noqa: E402

"""
The Phase 5.2 flow, end to end, against real PostgreSQL.

WHY THESE COMMIT FOR REAL

The isolation tests in tests/permissions run inside a transaction that
is rolled back, which is the right pattern when nothing commits. It
cannot work here, because the thing under test IS a commit: WebApproval
must make its row visible to a SEPARATE session, and "visible to
another connection" is precisely what an uncommitted row is not.

So these commit, and clean up after themselves in a `finally`. Each
test creates its own user; deleting that user cascades to the agents,
conversations and approvals it owns. Audit rows do not cascade - by
design, they outlive their subject - so they are removed explicitly.
"""


REGISTRY = build_registry()

CREATE_ISSUE = REGISTRY.require("github_create_issue")
LIST_ISSUES = REGISTRY.require("github_list_issues")

ARGS = {"owner": "octocat", "repo": "hello", "title": "hi"}


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
    Run one async test body against the real database, then clean up.

    Returns False when the database is unreachable so the test can
    report a skip rather than a failure.
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
                    # Audit rows hold no foreign key to the agent - that
                    # is the point of them - so nothing cascades them
                    # away. Explicit cleanup, by the tenant column.
                    await db.execute(
                        delete(AuditLog).where(
                            AuditLog.user_id == user_id
                        )
                    )
                    await db.execute(delete(User).where(User.id == user_id))

                await db.commit()

            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


async def _setup(maker, created, *, scopes=("github:*:write",)):

    async with maker() as db:

        user = User(
            email=f"approval-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="not-a-real-hash",
        )
        db.add(user)
        await db.flush()

        created.append(user.id)

        detail = await agent_service.create_agent(
            db,
            ENGINE,
            user.id,
            AgentCreate(name="Writer", system_prompt="test"),
        )

        # create_agent already seeds the READ wildcards (Phase 5.1), so
        # only the write grant is added here - re-adding a read scope
        # would hit uq_agent_scopes_agent_id, which is the constraint
        # doing its job.
        for scope in scopes:
            db.add(
                AgentScope(agent_id=detail.id, scope=scope, granted_by=user.id)
            )

        # BOTH gates, because Phase 5.1 checks both. create_agent
        # leaves write tools switched off, so granting the scope alone
        # is not enough to reach the approval step - the re-validation
        # would refuse the call before a human ever saw it.
        await agent_service.set_tools(
            db,
            ENGINE,
            user.id,
            detail.id,
            {"github_create_issue": AgentToolWrite(enabled=True)},
        )

        conversation = await conversation_service.create_conversation(
            db, user.id, detail.id, ConversationCreate(title="t")
        )

        await db.commit()

        return user.id, detail.id, conversation.id


def _handler(db, notifier, user_id, agent_id, conversation_id, *, timeout=5.0,
             events=None, overrides=None):

    def on_event(name, data):
        if events is not None:
            events.append((name, data))

    return WebApproval(
        db,
        notifier,
        conversation_id=conversation_id,
        agent_id=agent_id,
        agent_name="Writer",
        user_id=user_id,
        overrides=overrides,
        timeout_seconds=timeout,
        on_event=on_event,
    )


async def _wait_for_row(maker, agent_id, tries=200):
    """
    Poll ANOTHER session until the approval row appears.

    This is the assertion, not just a helper. If WebApproval flushed
    without committing, this loop would spin until it gave up - the row
    would exist only inside the turn's own open transaction, which is
    the exact bug that makes an approval impossible to resolve.
    """

    for _ in range(tries):
        async with maker() as other:
            row = await other.scalar(
                select(PendingApproval).where(
                    PendingApproval.agent_id == agent_id,
                    PendingApproval.status == str(ApprovalStatus.PENDING),
                )
            )

            if row is not None:
                return row

        await asyncio.sleep(0.02)

    return None


# ---------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------


def test_approving_wakes_the_turn_and_allows_the_call():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()
        events: list = []

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id,
                events=events,
            )

            # The turn parks here, exactly as it does inside the
            # executor.
            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            assert row is not None, "the row was never committed"

            # A SEPARATE session, as the real endpoint has.
            async with maker() as api_db:
                result = await approval_service.resolve(
                    api_db, notifier, user_id, row.id, approved=True
                )

            assert result.status == str(ApprovalStatus.APPROVED)

            assert await asyncio.wait_for(task, timeout=5) is True

        names = [name for name, _ in events]

        assert "approval_required" in names
        assert "approval_resolved" in names

    if not run_committed(body):
        _skipped("test_approving_wakes_the_turn_and_allows_the_call")


def test_the_dialog_receives_the_real_argument_values():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()
        events: list = []

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id,
                events=events,
            )

            task = asyncio.create_task(
                approval.request(
                    CREATE_ISSUE,
                    {**ARGS, "body": "ship it", "api_key": "ghp_secret"},
                )
            )

            row = await _wait_for_row(maker, agent_id)

            async with maker() as api_db:
                await approval_service.resolve(
                    api_db, notifier, user_id, row.id, approved=False
                )

            await asyncio.wait_for(task, timeout=5)

        payload = dict(row.arguments)

        # VALUES, not just keys. Seeing "attacker@evil.com" is the
        # entire defence against a prompt injection that got this far.
        assert payload["owner"] == "octocat"
        assert payload["title"] == "hi"

        # ...except secrets, which redact_arguments strips on the way in.
        assert payload["api_key"] == "***redacted***"

    if not run_committed(body):
        _skipped("test_the_dialog_receives_the_real_argument_values")


# ---------------------------------------------------------------------
# Saying no, and never answering
# ---------------------------------------------------------------------


def test_denying_stops_the_call():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id
            )

            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            async with maker() as api_db:
                await approval_service.resolve(
                    api_db, notifier, user_id, row.id, approved=False
                )

            assert await asyncio.wait_for(task, timeout=5) is False

    if not run_committed(body):
        _skipped("test_denying_stops_the_call")


def test_timeout_denies_and_marks_the_row_expired():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id,
                timeout=0.3,
            )

            # DENY ON TIMEOUT, never allow. Silence is not consent.
            assert await approval.request(CREATE_ISSUE, ARGS) is False

        async with maker() as db:
            row = await db.scalar(
                select(PendingApproval).where(
                    PendingApproval.agent_id == agent_id
                )
            )

            assert row.status == str(ApprovalStatus.EXPIRED)

            # NULL resolver: "the window closed" is a different event
            # from "a person said no", and the difference is the whole
            # reason resolved_by is nullable.
            assert row.resolved_by is None

    if not run_committed(body):
        _skipped("test_timeout_denies_and_marks_the_row_expired")


# ---------------------------------------------------------------------
# The TOCTOU test - the one that matters most
# ---------------------------------------------------------------------


def test_revoking_the_scope_mid_approval_denies_even_after_approve():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id
            )

            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            async with maker() as api_db:

                # The user changes their mind in another tab WHILE the
                # dialog is open. The turn's DatabaseScopePolicy froze
                # its permission sets minutes ago and would happily
                # allow this call.
                await api_db.execute(
                    delete(AgentScope).where(
                        AgentScope.agent_id == agent_id,
                        AgentScope.scope == "github:*:write",
                    )
                )
                await api_db.commit()

                # ...and then approves anyway.
                await approval_service.resolve(
                    api_db, notifier, user_id, row.id, approved=True
                )

            # Still denied. Consent is not capability: the handler
            # re-QUERIES the grants after waking rather than trusting
            # the snapshot it started with.
            assert await asyncio.wait_for(task, timeout=5) is False

        async with maker() as db:
            refreshed = await db.get(PendingApproval, row.id)

            assert refreshed.status == str(ApprovalStatus.DENIED)

    if not run_committed(body):
        _skipped(
            "test_revoking_the_scope_mid_approval_denies_even_after_approve"
        )


# ---------------------------------------------------------------------
# The resolve endpoint's own rules
# ---------------------------------------------------------------------


def test_another_user_cannot_resolve_my_approval():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)
        other_id, _, _ = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id,
                timeout=1.0,
            )

            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            async with maker() as api_db:
                try:
                    await approval_service.resolve(
                        api_db, notifier, other_id, row.id, approved=True
                    )
                    raise AssertionError("another user approved my call")

                except ApprovalNotFound:
                    # Not a Forbidden error. The router turns this into
                    # 404, so an id that is real looks exactly like one
                    # that is not.
                    pass

            # Nobody legitimate answered, so it times out and denies.
            assert await asyncio.wait_for(task, timeout=5) is False

    if not run_committed(body):
        _skipped("test_another_user_cannot_resolve_my_approval")


def test_resolving_twice_is_a_conflict():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id
            )

            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            async with maker() as api_db:

                await approval_service.resolve(
                    api_db, notifier, user_id, row.id, approved=True
                )

                try:
                    await approval_service.resolve(
                        api_db, notifier, user_id, row.id, approved=False
                    )
                    raise AssertionError("a resolved approval was re-resolved")

                except ApprovalAlreadyResolved:
                    # 409, never a silent 200. A replayed click must not
                    # report success for something that did not happen.
                    pass

            await asyncio.wait_for(task, timeout=5)

    if not run_committed(body):
        _skipped("test_resolving_twice_is_a_conflict")


def test_resolution_is_audited():

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id
            )

            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            async with maker() as api_db:
                await approval_service.resolve(
                    api_db, notifier, user_id, row.id, approved=True
                )

            await asyncio.wait_for(task, timeout=5)

        async with maker() as db:
            actions = list(
                await db.scalars(
                    select(AuditLog.action).where(
                        AuditLog.resource_id == agent_id
                    )
                )
            )

            # In the SAME log as scope changes: "what was this agent
            # allowed to do, and what did a human let it do?" is one
            # question.
            assert "approval.approved" in actions

    if not run_committed(body):
        _skipped("test_resolution_is_audited")


# ---------------------------------------------------------------------
# Who gets asked at all
# ---------------------------------------------------------------------


def test_reads_are_not_sent_for_approval():
    handler = DeferredApproval()

    assert handler.requires(CREATE_ISSUE) is True
    assert handler.requires(LIST_ISSUES) is False


def test_the_per_agent_override_wins_in_both_directions():
    # The setting that was stored, displayed, and never read until now.
    trusting = DeferredApproval({"github_create_issue": False})
    cautious = DeferredApproval({"github_list_issues": True})

    assert trusting.requires(CREATE_ISSUE) is False
    assert cautious.requires(LIST_ISSUES) is True


def test_deferred_approval_reports_without_executing():

    async def body():
        handler = DeferredApproval()

        allowed = await handler.request(CREATE_ISSUE, ARGS)

        return allowed, handler.pending

    allowed, pending = asyncio.run(body())

    assert allowed is False
    assert len(pending) == 1
    assert pending[0].tool_name == "github_create_issue"

    # Keys only on this path: nobody is going to be shown these, so
    # the values would be logged and read by no one.
    assert set(pending[0].arguments.values()) == {"..."}


# ---------------------------------------------------------------------
# CRITICAL tools can never be set to "always allow"
# ---------------------------------------------------------------------


def test_critical_tools_may_not_be_auto_approved():
    from api.approvals import may_auto_approve

    critical = [t for t in REGISTRY.all() if t.risk_level.value == "critical"]

    assert critical, "the fixture should contain at least one CRITICAL tool"

    for tool in critical:
        assert may_auto_approve(tool) is False

    # Everything else is the user's call to make.
    assert may_auto_approve(CREATE_ISSUE) is True


def test_switching_approval_off_for_a_critical_tool_is_ignored():

    async def body(maker, created):

        user_id, agent_id, _ = await _setup(maker, created)

        critical = next(
            t for t in REGISTRY.all() if t.risk_level.value == "critical"
        )

        async with maker() as db:

            detail = await agent_service.set_tools(
                db,
                ENGINE,
                user_id,
                agent_id,
                {
                    critical.name: AgentToolWrite(
                        enabled=True,
                        requires_approval=False,   # the user asks...
                    )
                },
            )
            await db.commit()

        row = next(t for t in detail.tools if t.tool_name == critical.name)

        # ...and is silently corrected. Enforced in the service, so a
        # client that skips the UI gets the same answer.
        assert row.enabled is True
        assert row.requires_approval is True

    if not run_committed(body):
        _skipped("test_switching_approval_off_for_a_critical_tool_is_ignored")


def test_expire_stale_recovers_abandoned_approvals():
    """
    The recovery path for a turn that died mid-wait.

    Normally the waiting turn expires its own row. If the worker was
    restarted there is nobody left to do that, and the row would sit
    'pending' forever - a question in the user's inbox that can never
    be answered.
    """

    async def body(maker, created):

        user_id, agent_id, conversation_id = await _setup(maker, created)

        notifier = ApprovalNotifier()

        async with maker() as turn_db:

            approval = _handler(
                turn_db, notifier, user_id, agent_id, conversation_id,
                timeout=0.2,
            )

            task = asyncio.create_task(approval.request(CREATE_ISSUE, ARGS))

            row = await _wait_for_row(maker, agent_id)

            # Simulate the worker vanishing: force the row's deadline
            # into the past and drop the waiter on the floor.
            async with maker() as db:
                stale = await db.get(PendingApproval, row.id)
                stale.expires_at = stale.created_at
                await db.commit()

            await asyncio.wait_for(task, timeout=5)

            # Put it back to pending, as an abandoned row would be.
            async with maker() as db:
                stale = await db.get(PendingApproval, row.id)
                stale.status = str(ApprovalStatus.PENDING)
                stale.resolved_at = None
                await db.commit()

            async with maker() as db:
                swept = await approval_service.expire_stale(db)
                await db.commit()

                assert swept >= 1

                refreshed = await db.get(PendingApproval, row.id)
                assert refreshed.status == str(ApprovalStatus.EXPIRED)

    if not run_committed(body):
        _skipped("test_expire_stale_recovers_abandoned_approvals")
