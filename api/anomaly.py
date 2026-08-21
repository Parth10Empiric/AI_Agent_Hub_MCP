from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Iterable

from core.logging import get_logger


logger = get_logger(__name__)


"""
Noticing a burst (Phase 5.6).

Mass exfiltration needs VOLUME. One shared Drive file is a mistake; two
hundred in ten minutes is an attack, and the difference between those
two is a number - not something a policy check on a single call can
ever see, because each call in isolation looks exactly like the one the
user asked for.

WHAT THIS IS NOT

It is not a control. It does not block anything, and it must not: a
detector that refuses is a rate limiter, that belongs in Phase 5.7, and
one written by accident here would refuse the wrong things.

This RECORDS and RAISES A SIGNAL. The value of a signal is that
somebody sees it - so it is deliberately simple, deliberately noisy
about the rare case, and silent about the ordinary one.

WHY THRESHOLDS AND NOT MACHINE LEARNING

Phase5.md's "what I would NOT build yet" list says it plainly: simple
thresholds first. A model that learns what is normal needs months of
normal to learn from, and until then it is a random number generator
with a confidence score attached.
"""


# Per agent, per hour. Starting points, tuned by watching real usage.
#
# ADMIN is far tighter than WRITE because ADMIN is the operation that
# hands data to someone else - google_drive_create_permission is how a
# document reaches an address the user has never heard of.
# DELIBERATELY BELOW the Phase 5.7 enforcement limits
# (write_ops_per_hour = 50, dangerous_ops_per_hour = 5).
#
# Detection that fires BEFORE enforcement means a warning appears in
# the log before any user sees an error - the difference between
# finding out from a dashboard and finding out from a support ticket.
#
# If you raise the limits in settings, raise these with them, or the
# alert stops leading and starts confirming.
WRITE_BURST_THRESHOLD = 25
ADMIN_BURST_THRESHOLD = 3

WINDOW = timedelta(hours=1)


class BurstDetector:
    """
    A sliding one-hour window of mutating operations, per agent.

    In memory, and that is a real limitation: it resets on restart and
    each worker counts separately. Both are acceptable for a signal and
    would not be for a control - which is the other reason this is not
    the rate limiter. Phase 6's Redis makes the window shared.
    """

    __slots__ = ("_events",)

    def __init__(self) -> None:
        # agent_id -> deque[(when, operation)]
        self._events: dict[uuid.UUID, deque] = {}

    def record(
        self,
        agent_id: uuid.UUID,
        operations: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """
        Record one turn's operations, and return any alerts raised.

        Called AFTER the turn is persisted, never before - a detector
        that runs on the hot path is a detector that eventually costs a
        user their answer.
        """

        moment = now or datetime.now(timezone.utc)

        window = self._events.setdefault(agent_id, deque())

        for operation in operations:
            if operation in {"write", "delete", "admin"}:
                window.append((moment, operation))

        # Drop everything older than the window. Done on WRITE rather
        # than on a timer, so an agent that goes quiet costs nothing -
        # and an agent that never runs again is eventually forgotten by
        # the prune below.
        cutoff = moment - WINDOW

        while window and window[0][0] < cutoff:
            window.popleft()

        if not window:
            self._events.pop(agent_id, None)
            return []

        admin = sum(1 for _, op in window if op == "admin")
        writes = len(window)

        alerts: list[str] = []

        if admin >= ADMIN_BURST_THRESHOLD:
            alerts.append(
                f"{admin} ADMIN operations in the last hour"
            )

        if writes >= WRITE_BURST_THRESHOLD:
            alerts.append(
                f"{writes} mutating operations in the last hour"
            )

        for alert in alerts:
            # WARNING, not ERROR. Nothing is broken and nothing was
            # refused - this is a human-shaped question ("did you mean
            # to do that?"), and logging it as an error trains whoever
            # reads the log to ignore errors.
            logger.warning(
                "anomaly: agent %s - %s", agent_id, alert
            )

        return alerts

    def count(self, agent_id: uuid.UUID) -> int:
        """How many mutating operations are in the window right now."""

        return len(self._events.get(agent_id, ()))

    def reset(self) -> None:
        self._events.clear()


# One per process, like the approval notifier. Built at import rather
# than on app.state because it holds nothing that needs configuring and
# nothing that needs closing.
detector = BurstDetector()
