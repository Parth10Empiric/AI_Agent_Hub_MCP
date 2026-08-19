from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent.schemas import ToolDefinition


@dataclass(slots=True)
class PendingApproval:
    """A call that was blocked because a human was not available."""

    tool_name: str
    operation: str
    risk_level: str
    arguments: dict


class WebApproval:
    """
    The ApprovalHandler for HTTP requests. It never blocks, and for now
    it always says no.

    WHY IT CANNOT SIMPLY ASK

    ConsoleApproval calls input() and waits. That works in a terminal
    because there is exactly one human sitting in front of exactly one
    process. Over HTTP neither is true:

        - there is no terminal to prompt
        - an HTTP request cannot wait ten minutes for a human
        - the person may have closed the tab

    The real solution makes the agent turn SUSPENDABLE: persist a
    pending approval, return "awaiting_approval" to the client, and
    re-enter the loop when the user clicks Approve in a separate
    request. That is scheduled for Phase 5, with a durable version in
    Phase 6 once Redis exists.

    WHY DENY RATHER THAN AUTO-APPROVE

    Auto-approving would be one line and is the wrong line. The agent
    would silently create issues, send Slack messages and share Drive
    files, with the approval flag doing nothing - and the flag would
    still LOOK enabled in the UI, which is worse than not having it.

    Denying is honest. The call is refused, the refusal is recorded on
    the ExecutionRecord as DENIED (not FAILED - the system worked
    correctly), and the model is told why, so it can explain the
    situation to the user instead of guessing.

    The denied calls are collected so the endpoint can report exactly
    what would have needed confirming.
    """

    __slots__ = ("pending", "_on_pending")

    def __init__(
        self,
        on_pending: Callable[[PendingApproval], None] | None = None,
    ) -> None:
        self.pending: list[PendingApproval] = []
        self._on_pending = on_pending

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:

        record = PendingApproval(
            tool_name=tool.name,
            operation=tool.operation.value,
            risk_level=tool.risk_level.value,

            # Argument keys only, never values. This object may be
            # logged or streamed to a browser, and the values can
            # contain a message body or a file path the user would not
            # expect to see leave the request.
            arguments={k: "..." for k in arguments},
        )

        self.pending.append(record)

        if self._on_pending is not None:
            self._on_pending(record)

        return False
