"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  ApprovalNotice,
  refusedFromExecutions,
} from "./approval-notice";
import { ApprovalRequest } from "@/components/approvals/approval-request";
import { Markdown } from "./markdown";
import { ToolTimeline } from "./tool-timeline";
import type { LiveTurn } from "@/lib/hooks/use-chat";
import type { ExecutionRead, MessageRead } from "@/lib/types";

/**
 * The conversation.
 *
 * Rows come from two places: `history` is what the server has stored,
 * and `live` is the turn currently in flight. Keeping them separate
 * means a refresh mid-turn cannot leave a phantom message on screen -
 * whatever the server has is the whole truth, and `live` disappears the
 * moment history is reloaded.
 */
export function MessageList({
  history,
  live,
  agentId,
  scrollParentRef,
  hasMore = false,
  loadingOlder = false,
  onLoadOlder,
  conversationKey,
}: {
  history: MessageRead[];
  live: LiveTurn | null;

  // Only so a refusal can link to the screen that fixes it. The list
  // itself does not care which agent is talking.
  agentId?: string;

  // The element that actually scrolls. Owned by the page, because the
  // page decides the layout - but the scrolling BEHAVIOUR belongs here
  // with the rows that cause it.
  scrollParentRef?: React.RefObject<HTMLDivElement | null>;

  hasMore?: boolean;
  loadingOlder?: boolean;
  onLoadOlder?: () => void;

  // Changes when a different conversation is opened, so the view
  // re-anchors to ITS newest message rather than keeping the last
  // one's scroll position.
  conversationKey?: string;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const topRef = useRef<HTMLDivElement>(null);

  // The scroll height captured just BEFORE older messages are
  // prepended. See the layout effect below - without it the reader is
  // thrown backwards through the conversation every time a page loads.
  const restoreRef = useRef<number | null>(null);

  // Is the view following the newest message?
  //
  // A ref, not state: it is read inside effects and observers, and must
  // never itself cause a render. Starts true because a conversation
  // opens pinned to the bottom and stays there until someone scrolls
  // away deliberately. See the two listeners below for why only a
  // GESTURE may set it false.
  const stickRef = useRef(true);

  // The list element, watched for height changes. See the follow
  // effect: content grows for reasons no dependency array can list.
  const contentRef = useRef<HTMLDivElement>(null);

  /**
   * Has the view been placed at the newest message yet?
   *
   * A CHAT OPENS AT THE END. That is not a preference, it is what the
   * newest message being the point of the screen means - and getting
   * there is a separate job from following it afterwards.
   *
   * It has to be STATE, not a ref: the paging observer below must stay
   * switched off until this is done, and only a state change can
   * re-run that effect.
   *
   * WHAT WENT WRONG WITHOUT IT
   *
   * The list rendered at scrollTop 0 - the top, the OLDEST message on
   * screen - and the smooth scroll to the bottom was still animating
   * when the paging observer, watching a sentinel that was right there
   * at the top, decided the reader wanted older messages. It fetched a
   * page. Then another. The conversation opened at its very first
   * message and walked backwards through history, which is exactly
   * upside down.
   */
  const [anchored, setAnchored] = useState(false);

  // Anchor to the newest message the moment there is anything to
  // anchor to. INSTANT, not smooth: this is not a movement the user
  // should watch, it is where the screen starts. A smooth scroll here
  // also takes hundreds of milliseconds, and the paging observer is
  // live for every one of them.
  useLayoutEffect(() => {
    if (anchored || history.length === 0) return;

    bottomRef.current?.scrollIntoView({ block: "end" });
    setAnchored(true);
  }, [anchored, history.length]);

  // A new conversation starts over: different rows, different bottom.
  useEffect(() => {
    setAnchored(false);
    stickRef.current = true;
  }, [conversationKey]);

  /**
   * Load the previous page when the top of the list comes into view.
   *
   * An IntersectionObserver rather than a scroll handler: it fires
   * once when the sentinel becomes visible instead of on every pixel
   * of movement, and it cannot miss a fast flick that jumps the
   * threshold in a single frame.
   */
  useEffect(() => {
    const parent = scrollParentRef?.current;
    const sentinel = topRef.current;

    // `anchored` is the guard that matters: until the view has been
    // put at the bottom, the sentinel is on screen by accident and
    // firing here walks the conversation backwards from its start.
    if (!anchored || !parent || !sentinel || !hasMore || !onLoadOlder) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;

        // Measured HERE, at the moment of the request, because by the
        // time the rows arrive the height has already changed.
        restoreRef.current = parent.scrollHeight;
        onLoadOlder();
      },
      {
        root: parent,
        // Start fetching a screenful early, so the page is usually
        // there before the reader reaches the end of what they have.
        rootMargin: "400px 0px 0px 0px",
      },
    );

    observer.observe(sentinel);

    return () => observer.disconnect();
  }, [anchored, scrollParentRef, hasMore, onLoadOlder, history.length]);

  /**
   * Keep the reader where they were when older messages arrive.
   *
   * Prepending rows pushes everything down by exactly the height of
   * what was added, so the fix is to scroll down by the same amount.
   * useLayoutEffect, not useEffect: this must happen in the same frame
   * as the paint, or the jump is visible.
   */
  useLayoutEffect(() => {
    const parent = scrollParentRef?.current;

    if (!parent || restoreRef.current === null) return;

    const added = parent.scrollHeight - restoreRef.current;
    restoreRef.current = null;

    if (added > 0) parent.scrollTop += added;
  }, [history, scrollParentRef]);

  /**
   * Should the view keep following the newest message?
   *
   * WHY THIS IS NOT "IS THE SCROLLBAR NEAR THE BOTTOM?"
   *
   * That was the first attempt, measured in a `scroll` listener, and it
   * broke following altogether. A `scroll` event fires for OUR OWN
   * scrolling too, and a smooth scroll fires a stream of them at every
   * intermediate position on the way down. Meanwhile the content keeps
   * growing underneath, so the animation is always chasing a bottom
   * that has moved - and the last event before it settles reports a
   * large distance. One reading of "far from the bottom" and following
   * switched itself off for the rest of the turn.
   *
   * Following is an INTENT, not a measurement, so the two directions
   * are treated differently:
   *
   *   turned OFF only by a user gesture - wheel, touch, a scrolling
   *     key. A programmatic scroll cannot produce any of those, so it
   *     can never switch itself off.
   *
   *   turned ON by position alone, from any scroll: arriving back at
   *     the bottom, however you got there, means "follow again".
   *
   * The asymmetry is the whole design. A rule that can only be
   * disabled deliberately cannot be disabled by accident.
   */
  useEffect(() => {
    const parent = scrollParentRef?.current;

    if (!parent) return;

    const nearBottom = () =>
      parent.scrollHeight - parent.scrollTop - parent.clientHeight < 120;

    // Any scroll may RESUME following. Never stop it.
    const onScroll = () => {
      if (nearBottom()) stickRef.current = true;
    };

    // A gesture may stop it. Only a person makes these.
    const onGesture = () => {
      if (!nearBottom()) stickRef.current = false;
    };

    // Keyboard scrolling, on the WINDOW.
    //
    // The scroll container is a plain div with no tabindex, so it never
    // receives keydown - a listener on it would be dead code, and
    // someone reading back with Page Up would be dragged to the bottom
    // by the next token that arrived.
    //
    // Only keys that actually scroll, so typing in the composer is not
    // mistaken for navigation.
    const SCROLLING_KEYS = new Set([
      "PageUp",
      "PageDown",
      "Home",
      "End",
      "ArrowUp",
      "ArrowDown",
      " ",
    ]);

    const onKey = (event: KeyboardEvent) => {
      if (SCROLLING_KEYS.has(event.key)) onGesture();
    };

    parent.addEventListener("scroll", onScroll, { passive: true });
    parent.addEventListener("wheel", onGesture, { passive: true });
    parent.addEventListener("touchmove", onGesture, { passive: true });
    window.addEventListener("keydown", onKey);

    return () => {
      parent.removeEventListener("scroll", onScroll);
      parent.removeEventListener("wheel", onGesture);
      parent.removeEventListener("touchmove", onGesture);
      window.removeEventListener("keydown", onKey);
    };
  }, [scrollParentRef]);

  /**
   * Follow the conversation as it grows.
   *
   * A ResizeObserver on the list, rather than an effect with a
   * dependency list. Height changes for reasons no dependency array
   * can enumerate: markdown finishing its layout, a tool row expanding,
   * the answer arriving, the status line growing by a word. The old
   * version listed four dependencies and missed all of those - it
   * followed whole messages and nothing else.
   *
   * `scrollTop = scrollHeight` rather than a smooth scrollIntoView.
   * Smooth animation is wrong while content streams: each frame aims at
   * a bottom that has already moved, so the view trails the text it is
   * meant to be showing. An instant pin looks like the page is simply
   * staying at the bottom, which is what it is doing.
   */
  useEffect(() => {
    const parent = scrollParentRef?.current;
    const content = contentRef.current;

    if (!anchored || !parent || !content) return;

    const follow = () => {
      if (!stickRef.current) return;
      parent.scrollTop = parent.scrollHeight;
    };

    const observer = new ResizeObserver(follow);

    observer.observe(content);
    follow();

    return () => observer.disconnect();
  }, [anchored, scrollParentRef]);

  // role='tool' rows are stored for the audit trail but are noise in a
  // transcript - the assistant's own summary already says what
  // happened, and the timeline shows the mechanics.
  const visible = history.filter(
    (message) => message.role === "user" || message.role === "assistant",
  );

  return (
    <div ref={contentRef} className="space-y-6">
      {/* THE TOP OF WHAT IS LOADED, not the top of the conversation.
          The observer above watches this; the text is what a reader
          sees if they get there before the fetch does. */}
      <div ref={topRef} aria-hidden />

      {hasMore && (
        <div className="flex justify-center py-2">
          {loadingOlder ? (
            <span className="text-xs text-muted-foreground">
              Loading earlier messages...
            </span>
          ) : (
            // A button as well as the observer. Scroll-triggered
            // loading is invisible to anyone navigating by keyboard,
            // and an infinite list with no other way in is a list they
            // cannot reach the start of.
            <button
              type="button"
              onClick={onLoadOlder}
              className="rounded-full border px-3 py-1 text-xs text-muted-foreground hover:bg-muted"
            >
              Load earlier messages
            </button>
          )}
        </div>
      )}

      {visible.map((message) => (
        <Turn key={message.id} role={message.role}>
          <Bubble role={message.role}>{message.content}</Bubble>

          {message.role === "assistant" && (
            <ApprovalNotice
              approvals={refusedFromExecutions(message.executions)}
              agentId={agentId}
            />
          )}

          {message.role === "assistant" && (
            <SettledTimeline executions={message.executions ?? []} />
          )}
        </Turn>
      ))}

      {live && (
        <>
          <Turn role="user">
            <Bubble role="user">{live.question}</Bubble>
          </Turn>

          <Turn role="assistant">
            <ToolTimeline rows={live.timeline} />

            {/* Every question this turn asked, answered or not.
                Rendered BELOW the timeline so the sequence reads in
                the order it happened: here is what I have done, here
                is what I wanted to do next.
                
                An ANSWERED one stays on screen showing what was
                chosen. It used to be removed the instant the server
                confirmed, so clicking Deny looked like the prompt had
                simply vanished - no confirmation, and nothing left
                after a reload. */}
            {live.approvalRequests.map((entry) => (
              <ApprovalRequest
                key={entry.event.approval_id}
                approval={entry.event}
                resolvedStatus={entry.status}
              />
            ))}

            {/* Refusals that have no card of their own - a tool
                stopped by its scope is denied below the model and
                nobody is ever asked about it, so the card list would
                not mention it at all.
                
                Read from the live TIMELINE rather than from
                `approvals_required`, which is the same source a
                reloaded conversation uses. One source, so the turn
                does not change what it says about itself the moment
                the page is refreshed. */}
            <ApprovalNotice
              approvals={refusedFromExecutions(
                live.timeline.map((row) => row.execution),
              ).filter(
                (refused) =>
                  !live.approvalRequests.some(
                    (entry) => entry.event.tool === refused.tool_name,
                  ),
              )}
              agentId={agentId}
            />

            {/* THREE STATES, AND A FAILED TURN IS NOT A BUSY ONE.

                This used to be a two-way choice: an answer, or the
                status line. A turn that fails has neither an answer nor
                anything still running - so it fell to the `else` and
                rendered a working indicator underneath its own error
                message:

                    Thinking  24s
                    Invalid or expired token

                The clock kept counting, because the status line's timer
                does not care why it is mounted. Nothing was running.
                The word "Thinking" next to the reason it stopped is the
                single most misleading thing this screen can say. */}
            {live.answer ? (
              <Bubble role="assistant">{live.answer}</Bubble>
            ) : live.error ? null : (
              <StatusLine live={live} />
            )}

            {live.error &&
              (live.rateLimited ? (
                /* AMBER, not red, and worded as a fact rather than a
                   failure. Nothing broke: the user has used their
                   allowance, and a red "error" box sends them looking
                   for a bug that does not exist. */
                <p
                  role="status"
                  data-testid="rate-limit-notice"
                  className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
                >
                  {live.error}
                </p>
              ) : (
                <p
                  role="alert"
                  className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
                >
                  {live.error}
                </p>
              ))}
          </Turn>
        </>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

/**
 * The "what is it doing right now" line.
 *
 * A silent spinner for thirty seconds feels broken. Naming the step -
 * and naming the service by the time it is known - is the difference
 * between "this is slow" and "this is stuck".
 */
/**
 * The timeline of a FINISHED turn: what worked, without the noise.
 *
 * WHY THIS DIFFERS FROM THE LIVE ONE
 *
 * While a turn runs, every attempt is worth watching - a failure is
 * the agent visibly trying something, and the next row shows what it
 * did about it. That is progress, and it is why the live timeline is
 * left exactly as it was.
 *
 * Afterwards, the same rows are archaeology. The answer above already
 * accounts for whatever went wrong, and a transcript full of red rows
 * reads as a broken product rather than as an agent that recovered.
 * One turn in these transcripts made four `create_issue` attempts that
 * were refused before the fifth succeeded; all five sat in the history
 * for ever, and the four said nothing the answer did not.
 *
 * WHAT IS NOT HIDDEN, AND WHY
 *
 *   denied    a refusal is not a failure - it is the permission system
 *             working, and it needs an action from the user. It stays,
 *             and it also has its own notice above.
 *
 *   the count a small "N failed" toggle remains, because a hidden
 *             failure is still a failure: when an answer looks wrong,
 *             the first question is what the agent tried. Hidden by
 *             default, one click to see, never gone.
 */
function SettledTimeline({ executions }: { executions: ExecutionRead[] }) {
  const [showFailed, setShowFailed] = useState(false);

  const kept = executions.filter(
    (execution) => execution.status !== "failed",
  );

  const failed = executions.filter(
    (execution) => execution.status === "failed",
  );

  const shown = showFailed ? executions : kept;

  if (executions.length === 0) return null;

  return (
    <>
      <ToolTimeline
        rows={shown.map((execution) => ({
          key: execution.id,
          tool_name: execution.tool_name,
          pending: false,
          execution,
        }))}
        // The total covers what is on screen. A duration that counts
        // rows the reader cannot see is a number they cannot check.
        totalMs={shown.reduce((sum, e) => sum + e.duration_ms, 0)}
      />

      {failed.length > 0 && (
        <button
          type="button"
          onClick={() => setShowFailed((open) => !open)}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          {showFailed
            ? "Hide failed attempts"
            : `${failed.length} failed ${
                failed.length === 1 ? "attempt" : "attempts"
              }`}
        </button>
      )}
    </>
  );
}

/**
 * What the agent is doing, and proof that it still is.
 *
 * THE COMPLAINT THIS REPLACES
 *
 * The old version was a pulsing dot and one of five fixed sentences. A
 * turn that runs for four minutes showed the same sentence for four
 * minutes, and a pulse that repeats every second looks identical
 * whether the work is progressing or the process has died. So the
 * honest reading of the screen, after ninety seconds of "Thinking...",
 * was that it had crashed - and users reload a page that was about to
 * answer them.
 *
 * Three things fix that, in order of how much they matter:
 *
 *   THE CLOCK       ticks on its own, once a second, with no help from
 *                   the server. It is the only element that keeps
 *                   moving during the long silence between a question
 *                   and the model's first token - the exact stretch
 *                   where the old UI looked dead.
 *
 *   THE ACTIVITY    names the thing being done: "Reading
 *                   github_get_file". Slow is fine when you can see
 *                   what is slow.
 *
 *   THE COUNTERS    round and token totals, which advance on their own
 *                   schedule and prove the loop is still turning even
 *                   when the activity has not changed.
 *
 * None of it is new information - all of it was already arriving on the
 * SSE stream and being dropped on the floor.
 */
function StatusLine({ live }: { live: LiveTurn }) {
  // A tick per second, purely so the rendered clock advances. The
  // interval is the POINT: every other number here changes only when
  // the server says something, and the silences are what look broken.
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const seconds = Math.max(0, Math.floor((now - live.startedAt) / 1000));

  const text = (() => {
    // The concrete thing wins over the category whenever there is one.
    if (live.activity && live.phase !== "waiting") return live.activity;

    switch (live.phase) {
      case "routing":
        return "Working out which tools to use";

      case "tools": {
        const service = live.routing?.services?.[0];
        return service ? `Searching ${service}` : "Using tools";
      }

      case "waiting":
        // Never "Thinking". The agent is not thinking, it is stopped
        // and waiting for this person - and telling them it is busy is
        // how a request sits unanswered until it expires.
        return "Waiting for your approval";

      case "writing":
        return "Writing the answer";

      default:
        return "Thinking";
    }
  })();

  // The clock is suppressed while waiting for approval: there, time
  // passing is the USER's doing, and a rising counter reads as the
  // system struggling rather than as a question nobody has answered.
  const waiting = live.phase === "waiting";

  const facts = [
    !waiting && formatElapsed(seconds),
    live.round > 1 && `step ${live.round}`,
    live.tokens > 0 && `${formatTokens(live.tokens)} tokens`,
  ].filter(Boolean) as string[];

  return (
    <div
      // polite, not assertive: a screen reader should hear each change
      // when it next pauses, never be interrupted mid-sentence by a
      // token counter.
      aria-live="polite"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground"
    >
      <span aria-hidden className="flex items-center gap-1">
        {/* Three dots on staggered delays. A single pulse is one shape
            appearing and disappearing, which a still frame cannot
            distinguish from frozen; a travelling wave cannot be
            mistaken for a stopped one. */}
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="size-1.5 animate-bounce rounded-full bg-foreground/40"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </span>

      <span className="font-medium text-foreground/80">{text}</span>

      {facts.length > 0 && (
        // Tabular numerals so the elapsed clock does not shuffle the
        // line sideways every time a digit changes width.
        <span className="tabular-nums text-xs">{facts.join(" · ")}</span>
      )}
    </div>
  );
}

/** "48s", "2m 05s" - short enough to sit inline, precise to the second. */
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;

  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;

  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

/** 1240 -> "1.2k". A precise token count is noise at this size. */
function formatTokens(tokens: number): string {
  return tokens < 1000
    ? String(tokens)
    : `${(tokens / 1000).toFixed(1)}k`;
}

function Turn({
  role,
  children,
}: {
  role: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={
        role === "user" ? "flex flex-col items-end" : "flex flex-col items-start"
      }
    >
      <span className="mb-1 text-xs font-medium text-muted-foreground">
        {role === "user" ? "You" : "Agent"}
      </span>

      <div className="min-w-0 max-w-[46rem]">{children}</div>
    </div>
  );
}

function Bubble({
  role,
  children,
}: {
  role: string;
  children: string | null | undefined;
}) {
  const isUser = role === "user";

  return (
    <div
      // A stable hook for tests. Asserting on CSS classes couples the
      // test suite to styling, so a colour change would break tests
      // that have nothing to do with colour.
      data-testid={isUser ? "user-message" : "agent-message"}
      className={
        isUser
          ? "rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-primary-foreground"
          : "rounded-2xl rounded-tl-sm bg-muted px-4 py-2.5"
      }
    >
      {isUser ? (
        // The user's own words are shown EXACTLY as typed. Someone who
        // writes `**test**` meant those asterisks - rendering them as
        // bold would silently change what they said.
        //
        // whitespace-pre-wrap keeps their line breaks; break-words stops
        // a long URL forcing the page to scroll sideways.
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
          {children}
        </p>
      ) : (
        // The agent writes Markdown, so it is rendered as Markdown.
        <Markdown>{children ?? ""}</Markdown>
      )}
    </div>
  );
}
