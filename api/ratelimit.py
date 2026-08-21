from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


"""
Rate limiting (Phase 5.7).

TWO REASONS, PULLING IN DIFFERENT DIRECTIONS

    COST           every agent turn is LLM tokens plus API calls.
                   A runaway loop is a bill.

    BLAST RADIUS   a compromised agent can only do so much damage
                   per hour.

They are different limits, on different keys, checked in different
places. One "100 requests a minute" rule satisfies neither: it does not
stop an agent quietly sharing 200 Drive files over an afternoon, and it
does not stop a loop that burns the model budget in thirty requests.

WHY A SLIDING WINDOW AND NOT A COUNTER PER HOUR

A fixed window resets on the clock, and the boundary is free:

    limit 30/hour
      10:59:58   30 turns   "within limit"
      11:00:02   30 turns   "within limit"
      -> 60 turns in four seconds

A sliding window keeps the timestamps and drops what has aged out, so
"the last hour" always means the last hour. It costs one float per
event, which for 300 tool calls an hour is nothing.

WHY THE INTERFACE COMES FIRST

Same seam as PermissionPolicy, CredentialStore and ApprovalNotifier.
Phase 6 swaps InMemoryRateLimiter for a Redis one and nothing that
calls it changes.

Until then: IN MEMORY MEANS PER WORKER. With four uvicorn workers the
effective limit is four times what it says. This project already runs
ONE worker because the approval notifier requires it (api/notifier.py),
so today the count is exact - but the two constraints must be lifted
together.
"""


@dataclass(frozen=True, slots=True)
class Decision:
    """
    The answer to "may this happen?", plus what to tell the caller.

    `retry_after` is not optional politeness. A client told "no" with
    no idea when to try again either gives up or hammers you - and
    hammering is the thing the limit exists to stop.

    `remaining` lets a well-behaved client slow down BEFORE it hits the
    wall, and lets the UI show a budget rather than a surprise.
    """

    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


@runtime_checkable
class RateLimiter(Protocol):
    """Anything that can count events in a window."""

    def check(self, key: str, limit: int, window: int) -> Decision:
        """Count one event and say whether it is allowed."""
        ...

    def peek(self, key: str, limit: int, window: int) -> Decision:
        """Report the budget WITHOUT consuming any of it."""
        ...


class InMemoryRateLimiter:
    """
    A sliding window log, per key.

    Deliberately synchronous. Counting timestamps in a dict is pure
    CPU, and an `async def` that never awaits is a promise of IO that
    does not exist - it would make every call site look like it might
    block, and hide the day a real backend does.

    The Redis implementation will be async, and the protocol above
    stays sync only as long as that is true; when Redis arrives, the
    protocol changes with it and the callers already await nothing
    else in that spot.
    """

    __slots__ = ("_windows", "_max_keys")

    def __init__(self, max_keys: int = 50_000) -> None:
        self._windows: dict[str, deque[float]] = {}

        # A bound, because every distinct key is a dict entry and keys
        # include user ids. Without it, one entry per user who ever
        # made a request, forever.
        self._max_keys = max_keys

    # -----------------------------------------------------------------

    def check(self, key: str, limit: int, window: int) -> Decision:

        now = time.monotonic()

        events = self._prune(key, now, window)

        if len(events) >= limit:
            return self._refused(events, limit, window, now)

        events.append(now)

        return Decision(
            allowed=True,
            limit=limit,
            remaining=max(0, limit - len(events)),
            retry_after=0,
            used=len(events),
        )

    def peek(self, key: str, limit: int, window: int) -> Decision:
        """
        The same answer, without spending anything.

        This is what the usage endpoint calls. A "how much have I got
        left?" request that consumed budget would be the funniest
        possible bug.
        """

        now = time.monotonic()

        events = self._prune(key, now, window)

        if len(events) >= limit:
            return self._refused(events, limit, window, now)

        return Decision(
            allowed=True,
            limit=limit,
            remaining=max(0, limit - len(events)),
            retry_after=0,
            used=len(events),
        )

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._windows.clear()
        else:
            self._windows.pop(key, None)

    # -----------------------------------------------------------------

    def _prune(self, key: str, now: float, window: int) -> deque[float]:

        events = self._windows.get(key)

        if events is None:
            if len(self._windows) >= self._max_keys:
                self._evict()

            events = deque()
            self._windows[key] = events

        cutoff = now - window

        while events and events[0] < cutoff:
            events.popleft()

        return events

    @staticmethod
    def _refused(
        events: deque[float],
        limit: int,
        window: int,
        now: float,
    ) -> Decision:
        """
        Refused, and honest about when to come back.

        retry_after is when the OLDEST event ages out - the exact
        moment a slot frees. Rounding up by a second avoids telling a
        client to retry a hair too early and be refused again, which is
        how a polite client turns into a hot loop.
        """

        oldest = events[0] if events else now

        wait = max(1, int(oldest + window - now) + 1)

        return Decision(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after=wait,
            used=len(events),
        )

    def _evict(self) -> None:
        """Drop the emptiest windows first - they are the stalest."""

        for key in sorted(self._windows, key=lambda k: len(self._windows[k]))[
            : max(1, self._max_keys // 10)
        ]:
            self._windows.pop(key, None)


def build_rate_limiter() -> RateLimiter:
    """Built once, at startup, and stored on app.state."""

    return InMemoryRateLimiter()
