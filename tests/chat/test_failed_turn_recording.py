from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from api.db.models import (  # noqa: E402
    Agent,
    Conversation,
    Message,
    ToolExecution,
    User,
)
from api.services import conversation_service  # noqa: E402
from api.services.chat_service import (  # noqa: E402
    TURN_FAILED_NOTE,
    TURN_STOPPED_NOTE,
    record_failed_turn,
)

"""
A turn that dies must not erase the record of what it did.

THE SESSION THIS COMES FROM

    05:53:31  user  "Yes, create the issues"
    05:53:31  approval requested   github_create_issue
    ...       22 approvals granted, one at a time, over three minutes
    05:56:52  approval granted     github_create_issue
              - and then nothing, ever

Sixteen issues existed on GitHub. The database had no assistant
message, no tool_executions rows, and a user message with nothing after
it: a chat showing a question the agent appeared to ignore.

WHY THE WORK SURVIVED AND THE RECORD DID NOT

Everything a turn does is spread across three places that fail
differently:

    GitHub       not transactional at all. An issue created is created.
    audit_log    committed as it goes, because an approval has to be
                 visible to the request that answers it. That is why
                 all 22 approvals survived.
    the turn     ONE transaction, committed at the very end - the user
                 message, every execution, the answer.

So a failure before that final commit discards exactly the record of
the work that happened, and keeps every trace of having asked
permission to do it.

Nothing can make the turn transactional once a real GitHub issue
exists. This makes the RECORD honest instead.
"""


def _database_url() -> str | None:
    try:
        from api.settings import get_settings

        return get_settings().database_url

    except Exception:
        return None


def run_in_transaction(body) -> bool:
    """One connection, one transaction, rolled back. Nothing persists."""

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
            await trans.rollback()
            await conn.close()
            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


async def _conversation(session: AsyncSession) -> Conversation:
    user = User(
        email=f"failed-turn-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="not-a-real-hash",
        full_name="Failed Turn",
    )
    session.add(user)
    await session.flush()

    agent = Agent(
        user_id=user.id,
        name="Recorder",
        system_prompt="You are a test agent.",
        model="test-model",
    )
    session.add(agent)
    await session.flush()

    conversation = Conversation(user_id=user.id, agent_id=agent.id)
    session.add(conversation)
    await session.flush()

    return conversation


def _execution(tool: str = "github_create_issue") -> dict:
    """One `tool_end` payload, the shape ExecutionRecord.to_dict emits."""

    return {
        # A STRING, like ExecutionRecord.execution_id - the column is
        # a String and asyncpg refuses a UUID object for it.
        "execution_id": uuid.uuid4().hex,
        "tool": tool,
        "server": "personal-mcp-server",
        "namespace": "github",
        "operation": "write",
        "risk_level": "medium",
        "status": "success",
        "started_at": "2026-08-26T05:54:38+00:00",
        "duration_ms": 1946.0,
        "attempts": 1,
        "arguments": {"owner": "a", "repo": "b", "title": "Bug"},
        "approved_by_user": True,
        "coercions": [],
        "error": None,
    }


async def _messages(session: AsyncSession, conversation_id) -> list[Message]:
    return list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
    )


# ---------------------------------------------------------------------


def test_a_turn_that_did_nothing_records_nothing():
    """
    THE RULE THIS TEST CHANGED, AND WHY.

    It used to assert the opposite: any failed turn got a note. That
    shipped, and the model provider immediately started answering 429
    ("you have reached your weekly usage limit"). Every turn failed
    before its first tool call, so every turn - including the first
    message of a brand new chat - was answered with:

        "This turn stopped before it finished... work that was already
         done cannot be undone by the failure."

    about work that never started. It reads as a fault in the product
    rather than a quota on an account, and it is unanswerable: there is
    nothing to check and nothing to undo.

    The note exists for ONE situation - the outside world changed and
    our record of it was rolled back. No executions means no such gap,
    and the error event already says what went wrong.
    """

    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [],
            reason="ResponseError",
        )

        assert await _messages(session, conversation.id) == []

    if not run_in_transaction(body):
        _skipped("test_a_turn_that_did_nothing_records_nothing")


def test_a_turn_that_did_something_still_answers_the_user():
    # The case the note is for: a question with nothing under it, while
    # sixteen GitHub issues exist that it created.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [_execution()],
            reason="CancelledError",
        )

        messages = await _messages(session, conversation.id)

        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == TURN_FAILED_NOTE

    if not run_in_transaction(body):
        _skipped("test_a_turn_that_did_something_still_answers_the_user")


def test_the_work_that_happened_is_kept():
    # THE ASSERTION THAT MATTERS. Twenty-two issues were created and
    # the timeline showed none of them.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [_execution(), _execution(), _execution()],
            reason="CancelledError",
        )

        rows = list(
            await session.scalars(
                select(ToolExecution).where(
                    ToolExecution.conversation_id == conversation.id
                )
            )
        )

        assert len(rows) == 3
        assert {r.tool_name for r in rows} == {"github_create_issue"}
        assert all(r.status == "success" for r in rows)
        assert all(r.approved_by_user is True for r in rows)

    if not run_in_transaction(body):
        _skipped("test_the_work_that_happened_is_kept")


def test_the_executions_hang_off_the_recovery_message():
    # Otherwise the timeline has nowhere to render them: the UI reads
    # executions through the message they belong to.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [_execution()],
        )

        messages = await _messages(session, conversation.id)
        row = await session.scalar(
            select(ToolExecution).where(
                ToolExecution.conversation_id == conversation.id
            )
        )

        assert row.message_id == messages[0].id
        assert row.agent_id == conversation.agent_id

    if not run_in_transaction(body):
        _skipped("test_the_executions_hang_off_the_recovery_message")


def test_the_failure_is_visible_in_the_routing_record():
    # "Why is there no answer here?" has to be answerable months later
    # from the row itself, not from a log that has rotated away.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [_execution(), _execution()],
            reason="TimeoutError",
        )

        messages = await _messages(session, conversation.id)

        assert messages[0].routing["turn_failed"] is True
        assert messages[0].routing["reason"] == "TimeoutError"
        assert messages[0].routing["executions_recovered"] == 2

    if not run_in_transaction(body):
        _skipped("test_the_failure_is_visible_in_the_routing_record")


def test_somebody_elses_conversation_is_never_touched():
    # It runs on the failure path with ids taken from a request. The
    # ownership check is not optional just because the caller is
    # apologising.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            uuid.uuid4(),          # a different user
            conversation.id,
            [_execution()],
        )

        assert await _messages(session, conversation.id) == []

    if not run_in_transaction(body):
        _skipped("test_somebody_elses_conversation_is_never_touched")


def test_a_missing_conversation_is_not_an_error():
    # The turn may have died before anything existed. A recovery path
    # that raises is a recovery path that makes things worse.
    async def body(session: AsyncSession) -> None:
        await record_failed_turn(
            session,
            uuid.uuid4(),
            uuid.uuid4(),
            [_execution()],
        )

    if not run_in_transaction(body):
        _skipped("test_a_missing_conversation_is_not_an_error")


# ---------------------------------------------------------------------
# The question, and the reason, after a turn that never started
# ---------------------------------------------------------------------


MODEL_LIMIT = (
    "Your model provider's usage limit has been reached. you (parthp) "
    "have reached your weekly usage limit."
)


def test_the_question_is_put_back_when_the_turn_took_it_down():
    """
    From the database, after the model provider began answering 429:

        06:37:02  assistant  "This turn stopped before it..."
        06:37:20  assistant  "This turn stopped before it..."
        06:37:32  assistant  "This turn stopped before it..."

    Three explanations and not one question - every user message had
    been rolled back with its turn. send_message adds it first, "saved
    BEFORE anything can fail", and one transaction around the whole
    turn defeats that intent completely.
    """

    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [],
            reason="ResponseError",
            question="list my repos",
            detail=MODEL_LIMIT,
        )

        messages = await _messages(session, conversation.id)

        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].content == "list my repos"

    if not run_in_transaction(body):
        _skipped("test_the_question_is_put_back_when_the_turn_took_it_down")


def test_the_note_says_what_actually_refused():
    # "The agent turn failed" is unanswerable. A quota message names
    # the account, the limit and the page that raises it.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [],
            reason="ResponseError",
            question="list my repos",
            detail=MODEL_LIMIT,
        )

        messages = await _messages(session, conversation.id)

        assert messages[1].content == MODEL_LIMIT
        assert "cannot be undone" not in messages[1].content

    if not run_in_transaction(body):
        _skipped("test_the_note_says_what_actually_refused")


def test_a_question_that_survived_is_not_duplicated():
    # A turn that died AFTER an approval committed still has its
    # question. Two copies of it would be worse than none.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await conversation_service.add_message(
            session,
            conversation,
            role="user",
            content="list my repos",
        )

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [_execution()],
            reason="CancelledError",
            question="list my repos",
        )

        messages = await _messages(session, conversation.id)

        assert [m.role for m in messages] == ["user", "assistant"]

    if not run_in_transaction(body):
        _skipped("test_a_question_that_survived_is_not_duplicated")


def test_a_stopped_turn_does_not_warn_about_work_it_never_did():
    # The note that shipped and was wrong: "check the affected service"
    # is essential after 16 issues were created, and alarming nonsense
    # after a request that never left the building.
    async def body(session: AsyncSession) -> None:
        conversation = await _conversation(session)

        await record_failed_turn(
            session,
            conversation.user_id,
            conversation.id,
            [],
            reason="ResponseError",
            question="hello",
        )

        messages = await _messages(session, conversation.id)

        assert messages[1].content == TURN_STOPPED_NOTE
        assert "nothing was changed" in messages[1].content

    if not run_in_transaction(body):
        _skipped("test_a_stopped_turn_does_not_warn_about_work_it_never_did")
