from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.anomaly import (  # noqa: E402
    ADMIN_BURST_THRESHOLD,
    WRITE_BURST_THRESHOLD,
    BurstDetector,
)

"""
Noticing volume (Phase 5.6).

One shared file is a mistake. Two hundred in ten minutes is an attack,
and no per-call permission check can tell them apart - each call in
isolation looks exactly like the one the user asked for.
"""


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _agent() -> uuid.UUID:
    return uuid.uuid4()


def test_reads_are_never_counted():
    detector = BurstDetector()
    agent = _agent()

    assert detector.record(agent, ["read"] * 500, now=NOW) == []
    assert detector.count(agent) == 0


def test_a_burst_of_admin_operations_raises_an_alert():
    detector = BurstDetector()
    agent = _agent()

    alerts = detector.record(
        agent,
        ["admin"] * ADMIN_BURST_THRESHOLD,
        now=NOW,
    )

    assert any("ADMIN" in alert for alert in alerts)


def test_the_admin_threshold_is_tighter_than_the_write_one():
    # ADMIN is the operation that hands data to someone else.
    # google_drive_create_permission is how a document reaches an
    # address the user has never heard of.
    assert ADMIN_BURST_THRESHOLD < WRITE_BURST_THRESHOLD


def test_a_normal_turn_is_silent():
    detector = BurstDetector()
    agent = _agent()

    # Three writes and a read - an ordinary "create the issue and tell
    # me about it" turn.
    assert detector.record(agent, ["write", "write", "read", "write"], now=NOW) == []


def test_operations_age_out_of_the_window():
    detector = BurstDetector()
    agent = _agent()

    detector.record(agent, ["admin"] * ADMIN_BURST_THRESHOLD, now=NOW)

    # An hour and a minute later, the old ones no longer count.
    later = NOW + timedelta(hours=1, minutes=1)

    assert detector.record(agent, ["admin"], now=later) == []
    assert detector.count(agent) == 1


def test_agents_are_counted_separately():
    detector = BurstDetector()

    noisy = _agent()
    quiet = _agent()

    detector.record(noisy, ["admin"] * ADMIN_BURST_THRESHOLD, now=NOW)

    # One agent misbehaving must not raise an alert about another.
    assert detector.record(quiet, ["admin"], now=NOW) == []


def test_a_quiet_agent_is_forgotten():
    # Otherwise this is a dict that only ever grows, one entry per
    # agent that ever ran a write.
    detector = BurstDetector()
    agent = _agent()

    detector.record(agent, ["write"], now=NOW)

    detector.record(agent, [], now=NOW + timedelta(hours=2))

    assert agent not in detector._events


def test_it_never_refuses_anything():
    """
    A detector that blocks is a rate limiter written by accident.

    Rate limiting is Phase 5.7, needs a SHARED window (this one is
    per process and resets on restart), and refuses on different
    grounds. record() returns strings and raises nothing, on purpose.
    """

    detector = BurstDetector()

    result = detector.record(_agent(), ["admin"] * 100, now=NOW)

    assert isinstance(result, list)
    assert all(isinstance(alert, str) for alert in result)
