from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.execution import redact_arguments
from agent.schemas import RiskLevel, ToolDefinition

from api.audit import AuditAction, ResourceType
from api.db.models import AgentScope, AgentTool, PendingApproval
from api.notifier import ApprovalNotifier
from api.policies import DatabaseScopePolicy
from api.services import audit_service
from core.logging import get_logger


logger = get_logger(__name__)


"""
Human approval over HTTP.

Phase 2's ConsoleApproval calls input() and blocks a thread. Over the
web neither half of that works: there is no terminal to prompt, and the
person may have closed the tab. What the web needs is for the TURN to
suspend - to stop, be woken by a completely separate request, and carry
on where it left off.

TWO HANDLERS, FOR TWO DIFFERENT ENDPOINTS

    WebApproval        suspends and waits.      POST .../messages/stream
    DeferredApproval   denies, and reports it.  POST .../messages

That split is deliberate. A plain JSON POST that hangs for five minutes
gets killed by a proxy, a load balancer, or the client's own timeout -
and the non-streaming endpoint exists for CLI clients, tests and
scheduled jobs, none of which have a human sitting in front of them.
"Here is what I would have needed you to confirm" is the correct answer
for all three.
"""


DEFAULT_TIMEOUT_SECONDS = 300


class ApprovalStatus(str, Enum):
    """The lifecycle of one approval."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"

    def __str__(self) -> str:
        # Python 3.10: a `str, Enum` still inherits Enum.__str__, so
        # str() would produce "ApprovalStatus.PENDING" while every ==
        # comparison kept passing. Same trap as api.scopes.AuditAction.
        return self.value


# Never offer "always allow" for these, and never auto-approve them.
NEVER_AUTO_APPROVE = frozenset({RiskLevel.CRITICAL})


def may_auto_approve(tool: ToolDefinition) -> bool:
    """
    May the user switch approval OFF for this tool?

    "Always allow create_issue for this agent" is a reasonable thing to
    want, and it is how an approval prompt stops being a reflex click.
    But it must never be offered for a CRITICAL tool -
    google_drive_delete_file, google_calendar_delete_acl_rule - where
    the damage cannot be inspected after the fact and often cannot be
    undone at all.

    Enforced in the SERVICE, not the UI. A checkbox the frontend
    declines to render is not a rule; a request that is refused is.

    Note what this deliberately is NOT: a separate agent_approval_rules
    table. The three modes that table would hold already exist as two
    columns on agent_tools:

        never          enabled = False
        auto_approve   enabled = True,  requires_approval = False
        always_ask     enabled = True,  requires_approval = True

    A third place to say the same thing is a third place for them to
    disagree.
    """

    return tool.risk_level not in NEVER_AUTO_APPROVE


@dataclass(slots=True)
class ApprovalRecord:
    """
    A call that was blocked because nobody could confirm it.

    Returned to the caller of the non-streaming endpoint so the API can
    say exactly what would have needed confirming.
    """

    tool_name: str
    operation: str
    risk_level: str
    arguments: dict


class DeferredApproval:
    """
    Refuse anything needing approval, and collect what was refused.

    This is Phase 3's WebApproval, kept and renamed. It is the right
    handler for a request that cannot wait.

    WHY DENY RATHER THAN AUTO-APPROVE

    Auto-approving would be one line and is the wrong line. The agent
    would silently create issues, send Slack messages and share Drive
    files, with the approval flag doing nothing - and the flag would
    still LOOK enabled in the UI, which is worse than not having it.

    Denying is honest. The call is refused, the refusal is recorded on
    the ExecutionRecord as DENIED (not FAILED - the system worked
    correctly), and the model is told why, so it can explain the
    situation to the user instead of guessing.
    """

    __slots__ = ("pending", "_overrides")

    def __init__(
        self,
        overrides: Mapping[str, bool] | None = None,
    ) -> None:
        self.pending: list[ApprovalRecord] = []
        self._overrides = dict(overrides or {})

    def requires(self, tool: ToolDefinition) -> bool:
        return _requires(tool, self._overrides)

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:

        self.pending.append(
            ApprovalRecord(
                tool_name=tool.name,
                operation=tool.operation.value,
                risk_level=tool.risk_level.value,

                # Keys only here, unlike WebApproval. Nothing is going
                # to ask a human about this call, so the values would
                # be shown to nobody and logged to everybody.
                arguments={key: "..." for key in arguments},
            )
        )

        return False


def _requires(
    tool: ToolDefinition,
    overrides: Mapping[str, bool],
) -> bool:
    """
    Does THIS agent need a human to confirm THIS tool?

        1. the user's per-tool setting, if they made one
        2. otherwise the Phase 2 classification

    Step 1 is what agent_tools.requires_approval has always meant and
    never did. A user who trusts an agent can switch approval off for a
    write; a cautious one can switch it on for a read - for instance a
    bulk export, which is technically a read and still moves data out
    of the account.
    """

    override = overrides.get(tool.name)

    if override is not None:
        return override

    return tool.requires_approval


class WebApproval:
    """
    Suspend the turn, ask the browser, and wait.

    THE SEQUENCE, AND WHY IT IS IN THIS ORDER

        1. register the wakeup       before anything is visible
        2. INSERT the row
        3. COMMIT                    <- the important one
        4. push the SSE event
        5. await, with a timeout
        6. re-read the row
        7. RE-CHECK PERMISSIONS      <- the other important one
        8. return True / False

    Step 3 does two jobs at once. It makes the row visible to the
    request that will resolve it - an uncommitted row does not exist
    for anybody else, so without this the approve endpoint 404s on
    something that is sitting right there in another connection's open
    transaction. And it RELEASES THE DATABASE CONNECTION back to the
    pool: the pool is 5 + 10 overflow, so fifteen people waiting on
    approvals inside open transactions would starve every other request
    in the API. Fifteen clicks should not take the product down.

    What it costs: the user's message and everything done so far are
    committed mid-turn. That is a fair trade - arguably an improvement,
    since a turn that dies later now leaves a readable history instead
    of nothing.

    WHAT IS *NOT* HELD DURING THE WAIT

    The MCP session lock. Approval is executor step 5 and the tool call
    is step 6, and ScopedSession takes the lock only around call_tool -
    so a human thinking for five minutes blocks nobody else's tools.
    That was built to stop a slow TURN blocking everyone; it turns out
    to cover slow HUMANS too.
    """

    __slots__ = (
        "pending",
        "_session",
        "_notifier",
        "_conversation_id",
        "_message_id",
        "_agent_id",
        "_agent_name",
        "_user_id",
        "_overrides",
        "_timeout",
        "_on_event",
    )

    def __init__(
        self,
        session: AsyncSession,
        notifier: ApprovalNotifier,
        *,
        conversation_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_name: str,
        user_id: uuid.UUID,
        message_id: uuid.UUID | None = None,
        overrides: Mapping[str, bool] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.pending: list[ApprovalRecord] = []
        self._session = session
        self._notifier = notifier
        self._conversation_id = conversation_id
        self._message_id = message_id
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._user_id = user_id
        self._overrides = dict(overrides or {})
        self._timeout = timeout_seconds
        self._on_event = on_event

    def requires(self, tool: ToolDefinition) -> bool:
        return _requires(tool, self._overrides)

    async def request(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> bool:

        record = await self._persist(tool, arguments)

        # Registered BEFORE the commit, so nobody can resolve this and
        # send a wakeup into the void while the waiter is still being
        # set up. See ApprovalNotifier.register.
        self._notifier.register(record.id)

        try:
            await self._session.commit()

            self._emit(
                "approval_required",
                {
                    "approval_id": str(record.id),
                    "tool": record.tool_name,
                    "operation": record.operation,
                    "risk_level": record.risk_level,
                    "arguments": record.arguments,
                    "expires_at": record.expires_at.isoformat(),
                    "timeout_seconds": int(self._timeout),
                },
            )

            notified = await self._notifier.wait(record.id, self._timeout)

            if not notified:
                await self._expire(record)
                self._emit(
                    "approval_resolved",
                    {
                        "approval_id": str(record.id),
                        "status": str(ApprovalStatus.EXPIRED),
                    },
                )
                return False

            # The event only says "go and look". The DATABASE is the
            # source of truth - a spurious wakeup, a double click or a
            # deny all arrive as the same signal.
            await self._session.refresh(record)

            approved = record.status == ApprovalStatus.APPROVED

            if approved and not await self._still_permitted(tool):
                # TIME-OF-CHECK TO TIME-OF-USE.
                #
                # DatabaseScopePolicy froze the permission sets when the
                # turn started, which is correct for a turn measured in
                # seconds. This one has been parked for up to five
                # minutes, and in that window the user may have revoked
                # the scope in another tab. Minutes is an enormous
                # TOCTOU window - most are microseconds - so the answer
                # is re-queried, never reused.
                await self._deny_revoked(record)
                approved = False

            self._emit(
                "approval_resolved",
                {
                    "approval_id": str(record.id),
                    "status": record.status,
                },
            )

            return approved

        finally:
            # Always. A turn that raises still has to drop its slot, or
            # the notifier leaks one Event per approval forever.
            self._notifier.discard(record.id)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    async def _persist(
        self,
        tool: ToolDefinition,
        arguments: dict,
    ) -> PendingApproval:

        now = datetime.now(timezone.utc)

        record = PendingApproval(
            conversation_id=self._conversation_id,
            message_id=self._message_id,
            agent_id=self._agent_id,
            user_id=self._user_id,
            tool_name=tool.name,
            operation=tool.operation.value,
            risk_level=tool.risk_level.value,

            # THE REAL VALUES, with secrets stripped and long strings
            # truncated. This is the whole defence against prompt
            # injection: a user who reads
            #
            #     email: attacker@evil.com
            #
            # clicks Cancel. Hide the values and the dialog is a
            # confirm button that means nothing.
            arguments=redact_arguments(arguments),

            status=str(ApprovalStatus.PENDING),
            expires_at=now + timedelta(seconds=self._timeout),
        )

        self._session.add(record)

        # PHASE 5.8: "asked but never answered" is a different story
        # from "denied", and the ratio between them is the health of
        # the whole approval feature. Only resolutions were recorded
        # before, so that ratio could not be computed at all.
        self._session.add(
            audit_service.entry(
                AuditAction.APPROVAL_REQUESTED,
                user_id=self._user_id,
                resource_type=ResourceType.AGENT,
                resource_id=self._agent_id,
                tool_name=tool.name,
                risk_level=tool.risk_level.value,
            )
        )

        # flush, not commit - the caller commits, because the commit is
        # what makes this visible and that ordering is the point.
        await self._session.flush()

        self.pending.append(
            ApprovalRecord(
                tool_name=record.tool_name,
                operation=record.operation,
                risk_level=record.risk_level,
                arguments=dict(record.arguments),
            )
        )

        return record

    async def _still_permitted(self, tool: ToolDefinition) -> bool:
        """
        Re-run the Phase 5.1 gate against CURRENT database state.

        Two fresh queries rather than the policy object the turn is
        holding. Reusing that object would re-apply the snapshot taken
        before the wait, which is exactly the stale answer this method
        exists to avoid.
        """

        granted = set(
            await self._session.scalars(
                select(AgentScope.scope).where(
                    AgentScope.agent_id == self._agent_id
                )
            )
        )

        enabled = set(
            await self._session.scalars(
                select(AgentTool.tool_name).where(
                    AgentTool.agent_id == self._agent_id,
                    AgentTool.enabled.is_(True),
                )
            )
        )

        return DatabaseScopePolicy(
            granted, enabled, self._agent_name
        ).permits(tool)

    async def _expire(self, record: PendingApproval) -> None:
        """
        Nobody answered in time.

        DENY ON TIMEOUT, never allow. The most likely reason a question
        went unanswered is that nobody was there to see it.

        resolved_by stays NULL - that is what distinguishes "the window
        closed" from "a person said no", and the ratio between those
        two is worth watching.
        """

        record.status = str(ApprovalStatus.EXPIRED)
        record.resolved_at = datetime.now(timezone.utc)

        # NO actor_user_id, and that is the record: nobody answered.
        # An expiry with a resolver would say a human declined, which
        # is a different thing and a worse one to get wrong.
        self._session.add(
            audit_service.entry(
                AuditAction.APPROVAL_EXPIRED,
                user_id=self._user_id,
                resource_type=ResourceType.AGENT,
                resource_id=self._agent_id,
                tool_name=record.tool_name,
                approval_id=str(record.id),
            )
        )

        await self._session.commit()

    async def _deny_revoked(self, record: PendingApproval) -> None:
        """The user approved, but the permission is gone."""

        logger.warning(
            "approval %s was approved but %s is no longer permitted",
            record.id,
            record.tool_name,
        )

        record.status = str(ApprovalStatus.DENIED)
        record.resolved_at = datetime.now(timezone.utc)

        await self._session.commit()

    def _emit(self, name: str, data: dict) -> None:
        if self._on_event is not None:
            self._on_event(name, data)
