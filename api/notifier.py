from __future__ import annotations

import asyncio
import uuid

from core.logging import get_logger


logger = get_logger(__name__)


"""
Waking a suspended agent turn.

One turn is parked inside `executor.execute`, awaiting a human. A
SEPARATE HTTP request - different connection, different session,
possibly a different browser tab - resolves the approval. Something has
to carry the news from the second to the first.

    turn task                          approve request
    ─────────────────────              ─────────────────────
    notifier.register(id)
    INSERT + COMMIT
    await notifier.wait(id)  ....
                                       UPDATE status='approved'
                                       COMMIT
                             ....◄──── notifier.notify(id)
    re-validate, continue

THIS IMPLEMENTATION IS SINGLE-PROCESS. THAT IS A REAL CONSTRAINT.

The Event objects live in one worker's memory, so the resolving request
must land on the same worker that is waiting. With `uvicorn --workers 4`
the request has a 3-in-4 chance of reaching a worker that has never
heard of that id: notify() does nothing, the turn waits out its full
timeout and denies. Nothing crashes, which is what makes it dangerous -
it just looks like every user is slow to click.

So: ONE worker until Phase 6 brings Redis. The class is deliberately
tiny and its three methods are exactly what a Redis pub/sub version
implements, so that swap is a new class, not a redesign - the same seam
trick that made DatabaseScopePolicy a one-line change in Phase 5.1.

WHAT THIS IS NOT

It is not the source of truth. The database is. This only says "go and
look again" - every waiter re-reads the row and re-checks permissions
after being woken. A lost notification therefore costs a delay, never a
wrong decision, and a spurious one costs nothing at all.
"""


class ApprovalNotifier:
    """In-process wakeups, keyed by approval id."""

    __slots__ = ("_events",)

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def register(self, approval_id: uuid.UUID | str) -> asyncio.Event:
        """
        Claim a slot BEFORE the row is committed.

        Order matters. If the row were committed first, a very fast
        human - or a scripted client - could resolve it and call
        notify() in the gap before the waiter existed. notify() would
        find nothing, and the turn would then wait five minutes for a
        signal that had already been sent.

        Registering first closes that window: by the time anyone else
        can SEE the approval, something is already listening for it.
        """

        key = str(approval_id)

        event = self._events.get(key)

        if event is None:
            event = asyncio.Event()
            self._events[key] = event

        return event

    async def wait(
        self,
        approval_id: uuid.UUID | str,
        timeout: float,
    ) -> bool:
        """
        Block until resolved or the timeout elapses.

        Returns True if notified, False on timeout. The caller must
        still read the row - "notified" does not mean "approved".
        """

        event = self.register(approval_id)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True

        except asyncio.TimeoutError:
            # DENY ON TIMEOUT, never allow. Silence is not consent -
            # the most likely reason nobody answered is that nobody
            # was there to see the question.
            return False

    def notify(self, approval_id: uuid.UUID | str) -> None:
        """
        Wake the waiter, if this process holds one.

        Never raises. This is called after the resolving transaction
        has already committed, so the decision is safe whatever happens
        here; failing the user's request because a wakeup did not land
        would be strictly worse than the turn timing out.
        """

        event = self._events.get(str(approval_id))

        if event is None:
            logger.debug(
                "no local waiter for approval %s "
                "(resolved on another worker, or already finished)",
                approval_id,
            )
            return

        event.set()

    def discard(self, approval_id: uuid.UUID | str) -> None:
        """
        Drop the slot once the turn is done with it.

        Without this the dict is a slow memory leak: one Event per
        approval, for the life of the process. Always called from a
        `finally`, because a turn that raises still has to clean up.
        """

        self._events.pop(str(approval_id), None)

    @property
    def waiting(self) -> int:
        """How many turns are parked. Useful in /health."""

        return len(self._events)


def build_approval_notifier() -> ApprovalNotifier:
    """Built once, at startup, and stored on app.state."""

    return ApprovalNotifier()
