from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from ollama import ResponseError
from sse_starlette.sse import EventSourceResponse

from api.db.models import Conversation
from api.deps import (
    AgentEngineDep,
    ApprovalNotifierDep,
    CredentialStoreDep,
    CurrentUser,
    DbDep,
    RateLimiterDep,
)
from api.schemas.chat import ChatRequest
from api.services import chat_service
from api.services.chat_service import AgentUnavailable, TurnLimitReached
from api.services.conversation_service import ConversationNotFound
from core.logging import get_logger


logger = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["chat"])


# Ends the queue. A sentinel rather than a flag because the consumer is
# blocked on queue.get() and needs something to arrive.
_DONE = object()


def _model_error_detail(status: int, message: str) -> str:
    """
    One sentence naming the model provider, the cause, and the fix.

    The provider's own words are kept - they carry the specifics, like
    which limit and where to raise it - with a lead that says WHOSE
    problem this is. Without that lead, "you have reached your weekly
    usage limit" reads as a limit inside this product.
    """

    lead = {
        401: "Your model provider rejected the request (not signed in).",
        403: "Your model provider rejected the request.",
        404: "The configured model was not found.",
        429: "Your model provider's usage limit has been reached.",
    }.get(status, "The model provider could not answer this request.")

    # Bounded, and stripped of our own wrapper noise. A provider that
    # returns a wall of text must not fill the chat with it.
    detail = message.strip()[:300]

    return f"{lead} {detail}".strip()


async def _record_failure(
    sessionmaker: Any,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    completed: list[dict],
    reason: str,
    *,
    question: str | None = None,
    detail: str | None = None,
) -> None:
    """
    Close off a conversation whose turn died, and never raise.

    A SECOND session, because the first one's transaction is already
    rolled back - reusing it would fail on the first statement, which
    is precisely the situation this is here to survive.

    Swallows everything. This runs on the failure path; a failure here
    would replace an actionable error event with a stack trace nobody
    asked for, and the user would still be looking at a question with
    no answer under it.
    """

    try:
        async with sessionmaker() as db:
            await chat_service.record_failed_turn(
                db,
                user_id,
                conversation_id,
                completed,
                reason=reason,
                question=question,
                detail=detail,
            )
            await db.commit()

    except Exception:
        logger.exception("could not record the failed turn")


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: uuid.UUID,
    payload: ChatRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbDep,
    engine: AgentEngineDep,
    notifier: ApprovalNotifierDep,
    store: CredentialStoreDep,
    limiter: RateLimiterDep,
) -> EventSourceResponse:
    """
    Run one agent turn and stream its progress as Server-Sent Events.

    WHY POST, WHEN SSE IS USUALLY A GET

    Phase3.md sketches `GET /stream`, and the browser's built-in
    EventSource only does GET. But EventSource CANNOT SET HEADERS - so
    it cannot send `Authorization: Bearer ...`, and this API is
    bearer-authenticated. The workarounds are all worse than the
    problem:

        token in the query string   -> ends up in access logs and
                                       browser history
        cookie-only auth for SSE    -> a second auth mechanism to
                                       maintain and get wrong

    A POST returning text/event-stream is read with fetch() and a
    ReadableStream, which sends headers normally. The frame format is
    identical; only the client API differs.

    WHY SSE AND NOT WEBSOCKETS

        SSE                          WebSockets
        one-way, server -> client    two-way
        plain HTTP, proxies fine     needs upgrade support
        auto-reconnect built in      you write reconnection
        trivial to load-balance      usually needs sticky sessions

    Everything pushed here is server-to-client. The user's input is an
    ordinary POST body. Nothing needs a socket.
    """

    provider = getattr(request.app.state, "mcp_provider", None)

    if provider is None or not provider.healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The tool server is not available right now.",
        )

    # VALIDATE BEFORE STREAMING.
    #
    # Once EventSourceResponse is returned the status line is already
    # 200 and the headers are sent. A 404 discovered later can only be
    # delivered as an "error" event inside a successful response, which
    # every HTTP client would have to be taught to interpret.
    #
    # So ownership is checked here, on the request-scoped session,
    # while a real status code can still be returned.
    owned = await session.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )

    if owned is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    sessionmaker = request.app.state.sessionmaker
    settings = request.app.state.settings
    user_id = current_user.id
    content = payload.content

    queue: asyncio.Queue = asyncio.Queue()

    # Every tool that finished, kept outside the turn's transaction.
    #
    # These are the only surviving copy if the turn dies before its
    # final commit - the ExecutionRecord objects go with it, and the
    # rows it would have written are rolled back. See
    # chat_service.record_failed_turn for the session this exists for.
    completed: list[dict] = []

    def on_event(name: str, data: dict) -> None:
        """
        Called by run_agent, from inside the turn.

        SYNCHRONOUS on purpose - that is the signature agent/loop.py
        emits with, and it must not be able to block the turn.
        put_nowait on an unbounded queue never waits and never raises,
        so a slow or vanished client cannot stall the agent.

        The loop's own "done" is RENAMED rather than forwarded.

        agent/loop.py emits `done` when the MODEL stops talking; this
        endpoint emits `done` when the turn is PERSISTED. Two events
        with the same name and different shapes would make a client
        either handle the answer twice or act on the wrong payload -
        the loop's has no message_id, because the row does not exist
        yet.

        Renaming keeps the useful early signal (the answer is ready,
        show it now) while leaving exactly one authoritative `done`.
        """

        if name == "done":
            name = "answer_ready"

        if name == "tool_end":
            completed.append(data)

        queue.put_nowait((name, data))

    async def run_turn() -> None:
        """
        The whole turn, in its own task and its OWN database session.

        NOT the request-scoped session from get_db.

        FastAPI tears down `yield` dependencies once the response is
        finished. For a streaming response the body is still being
        produced at that point, so the session could be closed out from
        under a turn that is halfway through writing rows. Owning a
        session here removes the question entirely.
        """

        try:
            async with sessionmaker() as db:
                try:
                    response = await chat_service.send_message(
                        db,
                        engine,
                        provider,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        content=content,
                        on_event=on_event,

                        # PHASE 5.2. Passing the notifier is what turns
                        # "deny anything needing approval" into "stop,
                        # ask the browser, and carry on when they
                        # answer". Only this endpoint may do it: it is
                        # the only one that can hold a connection open
                        # while a human reads a dialog.
                        notifier=notifier,
                        approval_timeout=settings.approval_timeout_seconds,

                        # PHASE 5.5: the caller's own credentials.
                        settings=settings,
                        store=store,
                        limiter=limiter,
                    )

                    await db.commit()

                    queue.put_nowait(
                        ("done", response.model_dump(mode="json"))
                    )

                except Exception:
                    await db.rollback()
                    raise

        except TurnLimitReached as exc:
            # Reported as an EVENT, not a status code: the response is
            # already a 200 text/event-stream by the time the turn
            # runs. The code and retry_after are what let the UI show
            # the same countdown it would for a real 429.
            queue.put_nowait(
                (
                    "error",
                    {
                        "detail": str(exc),
                        "code": "rate_limited",
                        "retry_after": exc.retry_after,
                    },
                )
            )

        except AgentUnavailable as exc:
            queue.put_nowait(("error", {"detail": str(exc), "code": "conflict"}))

        except ResponseError as exc:
            # THE MODEL PROVIDER REFUSED, AND SAID WHY.
            #
            # Ollama answers 429 with "you have reached your weekly
            # usage limit", 401 when a cloud session has expired, 400
            # when the model cannot do what was asked. Every one of
            # those is fixable by the person reading it - and every one
            # of them used to arrive as "The agent turn failed", which
            # is fixable by nobody.
            #
            # Same rule as ToolError.detail: the service's own sentence
            # is the actionable part, so it is passed through rather
            # than replaced by our category for it. It is a quota
            # message from a model host, not user data - there is
            # nothing in it to leak.
            status = getattr(exc, "status_code", 0) or 0

            logger.warning("model provider refused: %s %s", status, exc)

            detail = _model_error_detail(status, str(exc))

            # The SAME sentence, in the transcript and on the stream.
            # A live message that vanishes on reload is worse than one
            # that was never shown - the reader is left knowing they
            # saw something and not what it said.
            await _record_failure(
                sessionmaker,
                user_id,
                conversation_id,
                completed,
                type(exc).__name__,
                question=content,
                detail=detail,
            )

            queue.put_nowait(
                (
                    "error",
                    {
                        "detail": detail,
                        # A quota is not a fault. Reported with the same
                        # code as our own rate limit so the UI words it
                        # the same calm way - see the amber notice in
                        # message-list.tsx.
                        "code": (
                            "rate_limited" if status == 429 else "model_error"
                        ),
                        "retry_after": 0,
                    },
                )
            )

        except ConversationNotFound:
            queue.put_nowait(
                ("error", {"detail": "Conversation not found.", "code": "not_found"})
            )

        except Exception as exc:
            # The turn failed after the response started. There is no
            # status code left to change, so the failure is reported as
            # an event and logged in full on the server side.
            logger.exception("streaming turn failed")

            # AND THE CONVERSATION IS CLOSED OFF, in a fresh session.
            #
            # The turn's own session has just been rolled back, so the
            # user message survives only because an approval committed
            # mid-turn - and the assistant message, along with every
            # execution row, is gone. What is NOT gone is the work
            # itself: issues created, messages sent. Leaving the
            # transcript with a question and no answer tells the user
            # their request was ignored, when in fact it half happened.
            #
            # Its own try/except because a caller must never fail twice:
            # if this write fails too, the error event below is still
            # the thing the user needs.
            await _record_failure(
                sessionmaker,
                user_id,
                conversation_id,
                completed,
                type(exc).__name__,
                question=content,
            )

            queue.put_nowait(
                (
                    "error",
                    {
                        # Deliberately generic. A stack trace or a raw
                        # database error in a browser payload is an
                        # information leak.
                        "detail": "The agent turn failed.",
                        "code": "internal_error",
                        "error_type": type(exc).__name__,
                    },
                )
            )

        finally:
            queue.put_nowait((_DONE, None))

    async def publisher() -> AsyncIterator[dict[str, Any]]:

        task = asyncio.create_task(run_turn(), name="sse-turn")

        try:
            while True:

                name, data = await queue.get()

                if name is _DONE:
                    break

                yield {"event": name, "data": json.dumps(data, default=str)}

        finally:
            # Reached when the client disconnects mid-turn, too.
            #
            # The task is NOT cancelled: the turn may have already
            # created a GitHub issue or sent a Slack message, and it
            # must be allowed to finish persisting what it did. A user
            # closing a tab must not leave a half-recorded turn.
            if not task.done():
                try:
                    # Long enough to outlast an approval wait plus the
                    # tool call it authorises. A turn parked on a
                    # human is the most likely reason a task is still
                    # running here, and cutting it off at two minutes
                    # would abandon it halfway through recording what
                    # it did.
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=settings.approval_timeout_seconds + 120,
                    )

                except (TimeoutError, asyncio.CancelledError):
                    logger.warning("SSE turn still running after disconnect")

    return EventSourceResponse(
        publisher(),
        # A comment frame every 15s. Without it, proxies and load
        # balancers close a connection that has been quiet - and a turn
        # can easily think for 30 seconds before its first tool call.
        ping=15,
    )
