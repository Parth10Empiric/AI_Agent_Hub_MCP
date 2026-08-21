from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from api.budgets import key
from api.deps import CurrentUser, DbDep, RateLimiterDep, SettingsDep
from api.schemas.limit import LimitRead, LimitsRead


"""
What a user has left (Phase 5.7).

A limit nobody can see is a limit that arrives as a mystery failure.
This endpoint exists so the UI can show a budget shrinking BEFORE it
runs out - which turns "the agent stopped working" into "you have three
turns left this hour".

EVERYTHING HERE USES peek(), NOT check().

Asking how much budget you have must not spend any. A usage endpoint
that consumed a slot would make the meter wrong by exactly the number
of times the user looked at it - and a dashboard that polls would
exhaust the limit it was reporting on.
"""


router = APIRouter(prefix="/api/limits", tags=["limits"])


HOUR = 3600


@router.get("", response_model=LimitsRead)
async def get_limits(
    current_user: CurrentUser,
    session: DbDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    agent_id: uuid.UUID | None = Query(default=None),
) -> LimitsRead:
    """
    This user's current usage.

    `agent_id` is optional and adds the per-agent budgets. They are
    per-AGENT because that is the unit a user configures and the unit
    that gets compromised - so "which agent used them up?" has to have
    an answer.
    """

    if not settings.rate_limit_enabled:
        return LimitsRead(enabled=False, limits=[])

    limits: list[LimitRead] = []

    limits.append(
        _read(
            limiter,
            key(current_user.id, "turns"),
            settings.agent_turns_per_hour,
            HOUR,
            key_name="turns",
            label="Agent turns",
            description="Messages you have sent to any agent this hour.",
        )
    )

    limits.append(
        _read(
            limiter,
            key(current_user.id, "tool_calls"),
            settings.tool_calls_per_hour,
            HOUR,
            key_name="tool_calls",
            label="Tool calls",
            description="Actions your agents have taken this hour.",
        )
    )

    if agent_id is not None:

        limits.append(
            _read(
                limiter,
                key(agent_id, "write"),
                settings.write_ops_per_hour,
                HOUR,
                key_name="writes",
                label="Changes by this agent",
                description=(
                    "Things this agent has created or edited this hour."
                ),
            )
        )

        limits.append(
            _read(
                limiter,
                key(agent_id, "dangerous"),
                settings.dangerous_ops_per_hour,
                HOUR,
                key_name="dangerous",
                label="High-risk actions",
                description=(
                    "Sending, sharing and deleting - things that are hard "
                    "to undo."
                ),
            )
        )

    return LimitsRead(enabled=True, limits=limits)


def _read(
    limiter,
    bucket_key: str,
    limit: int,
    window: int,
    *,
    key_name: str,
    label: str,
    description: str,
) -> LimitRead:

    decision = limiter.peek(bucket_key, limit, window)

    return LimitRead(
        key=key_name,
        label=label,
        description=description,
        used=decision.used,
        limit=decision.limit,
        remaining=decision.remaining,
        resets_in=decision.retry_after,
        window_label="hour",
    )
