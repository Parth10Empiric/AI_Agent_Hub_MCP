from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.notifier import ApprovalNotifier  # noqa: E402

"""
Tests for the wakeup registry.

Pure asyncio - no database, no HTTP. The whole class is about ORDER
(register before commit, notify after commit), and order is exactly
what a unit test can pin down and an integration test cannot.
"""


def run(coro):
    return asyncio.run(coro)


def test_notify_wakes_a_waiter():

    async def body():
        notifier = ApprovalNotifier()
        key = uuid.uuid4()

        notifier.register(key)

        async def resolver():
            await asyncio.sleep(0.01)
            notifier.notify(key)

        asyncio.create_task(resolver())

        return await notifier.wait(key, timeout=2)

    assert run(body()) is True


def test_a_notification_sent_before_the_wait_is_not_lost():
    """
    The race the register-first rule exists for.

    A fast client can resolve an approval before the turn reaches its
    await. asyncio.Event REMEMBERS being set, so as long as the slot
    was registered first, the wait returns immediately instead of
    hanging for the full five minutes.
    """

    async def body():
        notifier = ApprovalNotifier()
        key = uuid.uuid4()

        notifier.register(key)      # 1. the turn claims its slot
        notifier.notify(key)        # 2. the human answers, fast

        return await notifier.wait(key, timeout=0.5)   # 3. the turn waits

    assert run(body()) is True


def test_timeout_returns_false():

    async def body():
        notifier = ApprovalNotifier()

        # Nobody ever answers. Silence must become "no" on its own.
        return await notifier.wait(uuid.uuid4(), timeout=0.05)

    assert run(body()) is False


def test_notifying_an_unknown_id_is_harmless():
    # Happens whenever a turn has already given up, or - once there is
    # more than one worker - when the resolving request lands somewhere
    # else. It must never raise: the decision is already committed, and
    # failing the user's request over a missed wakeup would be worse
    # than the turn timing out.
    notifier = ApprovalNotifier()

    notifier.notify(uuid.uuid4())


def test_discard_frees_the_slot():
    notifier = ApprovalNotifier()
    key = uuid.uuid4()

    notifier.register(key)
    assert notifier.waiting == 1

    notifier.discard(key)
    assert notifier.waiting == 0

    # Idempotent - `finally` blocks run on paths that already cleaned up.
    notifier.discard(key)


def test_uuid_and_string_keys_are_the_same_slot():
    # The turn holds a UUID; the HTTP path may carry a string. Two
    # dict entries for one approval would mean the waiter never hears
    # about it.
    notifier = ApprovalNotifier()
    key = uuid.uuid4()

    notifier.register(key)
    notifier.notify(str(key))

    assert run(notifier.wait(key, timeout=0.5)) is True
