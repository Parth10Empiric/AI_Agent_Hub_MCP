from __future__ import annotations

from pydantic import BaseModel, Field


class LimitRead(BaseModel):
    """
    One budget, as the UI shows it.

    `used` and `limit` rather than a percentage, because a percentage
    hides the size of the thing: "80% used" means something very
    different at 5 per hour and at 300.
    """

    key: str
    label: str
    description: str

    used: int
    limit: int
    remaining: int

    # Seconds until the oldest event ages out - i.e. when the first
    # slot frees. 0 when there is budget left, so the UI never shows a
    # countdown to something that has not happened.
    resets_in: int = 0

    window_label: str = "hour"

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


class LimitsRead(BaseModel):
    """
    GET /api/limits

    Everything a user is currently spending against, in one response.
    Two requests would mean the page renders half its meters from a
    stale answer.
    """

    enabled: bool = True
    limits: list[LimitRead] = Field(default_factory=list)
