"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { listMessages } from "@/lib/api/conversations";
import type { PendingApprovalEvent } from "@/components/approvals/approval-request";
import { streamTurn, type StreamEvent } from "./use-sse";
import type {
  ApprovalRequired,
  ApprovalStatus,
  ExecutionRead,
  MessageRead,
} from "@/lib/types";

/**
 * One row in the live tool timeline.
 *
 * `pending` rows exist only while a tool is running. They are created
 * by tool_start and replaced by the real ExecutionRead from tool_end.
 */
export interface TimelineRow {
  key: string;
  tool_name: string;
  pending: boolean;
  execution?: ExecutionRead;
}

/**
 * One approval question, and its answer once there is one.
 *
 * `status` is null while the turn is genuinely parked on it. Anything
 * else means the question is settled - by this tab, by another tab, or
 * by the clock running out - and the card renders that outcome instead
 * of a countdown.
 */
export interface TurnApproval {
  event: PendingApprovalEvent;
  status: ApprovalStatus | null;
}

export type TurnPhase =
  | "idle"
  | "routing"
  | "thinking"
  | "tools"
  // The turn is SUSPENDED on the server, waiting for this user to
  // approve or deny a tool call. Not "slow" - stopped, on purpose,
  // until someone answers.
  | "waiting"
  | "writing";

export interface LiveTurn {
  phase: TurnPhase;
  question: string;
  answer: string;
  timeline: TimelineRow[];
  routing?: { services: string[]; tools: number; confidence: number };

  // Calls the agent wanted to make and was refused, because they need a
  // human to confirm. Populated from the final `done` event, which
  // carries the whole ChatResponse.
  approvals: ApprovalRequired[];

  /**
   * Every approval this turn asked about, in the order it asked.
   *
   * WAS `pendingApproval: PendingApprovalEvent | null`, AND THAT WAS
   * THE BUG PEOPLE SAW.
   *
   * A single nullable slot meant `approval_resolved` had nowhere to
   * put the answer, so it set the slot to null - and the card
   * UNMOUNTED. Click Deny and the prompt simply disappeared: the
   * "Denied. Nothing was changed in your accounts." footer the card
   * renders was never on screen long enough to read, and after a
   * reload there was nothing at all.
   *
   * Keeping the list means an answered question stays where it was
   * asked, showing what the answer was. Which is the whole point of
   * asking it in the chat rather than in a modal.
   */
  approvalRequests: TurnApproval[];

  error?: string;

  // True when the turn stopped because of a rate limit rather than a
  // fault. The UI words those two very differently, and conflating
  // them sends people hunting for a bug that does not exist.
  rateLimited?: boolean;

  // --- Progress ------------------------------------------------------
  //
  // WHY A LIVE TURN HAS TO REPORT NUMBERS, NOT JUST A PHASE.
  //
  // The status line used to be a pulsing dot and one of five fixed
  // sentences. A turn that thinks for four minutes shows the same
  // sentence for four minutes, and a pulse that repeats every second
  // is indistinguishable from a pulse that is stuck - so the honest
  // reading of the screen, after ninety seconds of "Thinking...", is
  // that the thing has died.
  //
  // It had not: that turn was on round 14, calling its ninth tool. The
  // information existed and was thrown away, because `round_start` and
  // `tokens` had no case in applyEvent.
  //
  // A number that CHANGES is the difference between "working" and
  // "hung", and it is the only difference a user can see.

  // When the turn began, for the elapsed clock. Milliseconds since the
  // epoch rather than a Date, so a re-render cannot subtly re-create it.
  startedAt: number;

  // Which round the agent loop is on. Round 1 is the first question to
  // the model; each tool call it makes adds another.
  round: number;

  // Tokens read and written so far, summed across rounds. The best
  // available proxy for "how much work is this?" - and it moves even
  // while the model is thinking with no tools in sight.
  tokens: number;

  // The last concrete thing that happened, in the user's words:
  // "Reading github_get_file", "Looking for more tools". Distinct from
  // `phase`, which is a category and changes far less often.
  activity?: string;
}

/**
 * How many messages one page holds.
 *
 * Small enough that opening a long conversation is instant, big enough
 * that a normal one arrives whole and never pages at all. A chat
 * viewport shows roughly six to ten turns; 25 rows means the first
 * scroll up already has content under it.
 */
const PAGE_SIZE = 25;

/**
 * Everything the chat page needs: history, sending, and live progress.
 *
 * Deliberately a hook rather than logic inside the page component. The
 * page then only decides what things LOOK like, and this file owns what
 * the conversation DOES - which is the part worth testing and reusing.
 */
export function useChat(conversationId: string) {
  const queryClient = useQueryClient();

  const [history, setHistory] = useState<MessageRead[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [live, setLive] = useState<LiveTurn | null>(null);

  // --- Paging ------------------------------------------------------
  //
  // A chat that has run for a month is a chat with thousands of rows,
  // and every one of them used to be fetched, parsed, sorted and
  // rendered before the first word appeared. The cost is paid on
  // OPENING the conversation - the moment the user is least willing to
  // wait, and the moment they can least understand why.
  //
  // Only the newest page is fetched now. Older ones arrive when the
  // reader actually scrolls towards them, which for most conversations
  // is never.
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  // The in-flight guard, in a ref because scroll handlers fire faster
  // than React re-renders. See loadOlder.
  const loadingRef = useRef(false);

  // Which conversation the paging state describes. See loadHistory:
  // it tells a first load apart from a post-turn refresh.
  const initialisedFor = useRef<string | null>(null);

  // Held in a ref, not state: aborting is an imperative action and must
  // not cause a re-render, and the value must survive between renders.
  const abortRef = useRef<AbortController | null>(null);

  const loadHistory = useCallback(async () => {
    const page = await listMessages(conversationId, { limit: PAGE_SIZE });

    // FIRST LOAD OF THIS CONVERSATION, OR A REFRESH OF ONE ALREADY OPEN?
    //
    // The same function serves both, and they must not do the same
    // thing. A refresh runs after every turn, and replacing the list
    // with page one would throw away every older page the reader had
    // scrolled up to load - so a long turn would silently rewind the
    // conversation they were reading.
    const first = initialisedFor.current !== conversationId;

    if (first) {
      initialisedFor.current = conversationId;

      // The API returns newest-first (that is what makes cursor paging
      // work when scrolling up). A chat reads oldest-first, so sort a
      // COPY - .sort() and .reverse() both mutate, and mutating a
      // cached array would corrupt whatever else is holding it.
      setHistory([...page.items].sort(byConversationOrder));

      // Where the NEXT page starts, and whether there is one. Both come
      // from the server: it fetches limit+1 rows and reports the extra
      // one as `has_more`, so neither answer is guessed here.
      setCursor(page.next_cursor ?? null);
      setHasMore(page.has_more);
    } else {
      // Merge the newest page in and leave the paging state alone. The
      // cursor points at the OLDEST row loaded, which is further back
      // than anything page one knows about - overwriting it with page
      // one's cursor would make the next scroll re-fetch rows already
      // on screen.
      setHistory((prev) => {
        const seen = new Set(prev.map((message) => message.id));

        const fresh = page.items.filter(
          (message) => !seen.has(message.id),
        );

        return [...prev, ...fresh].sort(byConversationOrder);
      });
    }

    setHistoryLoading(false);
  }, [conversationId]);

  /**
   * Fetch the page ABOVE what is on screen.
   *
   * Keyset pagination, not offset: the cursor is the (created_at, id)
   * of the oldest row we hold, so a message arriving while the user
   * reads cannot shift a page boundary and make a row appear twice or
   * not at all. `limit`/`offset` has that bug by construction, and it
   * shows up exactly when a conversation is busy.
   *
   * Prepends. The list stays oldest-first, so older pages go on the
   * front and the reader's position is preserved by the caller (see
   * MessageList - it measures scrollHeight either side of this).
   */
  const loadOlder = useCallback(async () => {
    // Guarded on the REF, not the state. Two scroll events can fire
    // before React re-renders, and both would read the same stale
    // `loadingOlder === false` and fetch the same page twice.
    if (!cursor || loadingRef.current) return;

    loadingRef.current = true;
    setLoadingOlder(true);

    try {
      const page = await listMessages(conversationId, {
        limit: PAGE_SIZE,
        cursor,
      });

      setHistory((prev) => {
        // Dedupe by id. A turn that lands between two page fetches can
        // legitimately put the same row in both, and React would then
        // warn about duplicate keys AND render the message twice.
        const seen = new Set(prev.map((message) => message.id));

        const older = page.items.filter(
          (message) => !seen.has(message.id),
        );

        return [...older, ...prev].sort(byConversationOrder);
      });

      setCursor(page.next_cursor ?? null);
      setHasMore(page.has_more);
    } catch {
      // Leave the cursor where it is so the next scroll can retry.
      // Failing to load OLD messages must never disturb the live
      // conversation - the user is mid-read, not mid-send.
    } finally {
      loadingRef.current = false;
      setLoadingOlder(false);
    }
  }, [conversationId, cursor]);

  /**
   * loadHistory, but it can never throw.
   *
   * The raw one is allowed to reject - the initial load wants to know.
   * Every call made WHILE A TURN IS ON SCREEN goes through this
   * instead, because a rejected refetch there does not just fail to
   * refresh: it skips the `setLive(null)` that follows it, and a
   * non-null `live` is the state the composer will not send from.
   *
   * One network blip after a turn and the chat is dead until reload.
   * That is too much consequence for a refetch.
   */
  const refreshHistory = useCallback(async () => {
    try {
      await loadHistory();
    } catch {
      // Leave the history as it stands. The next turn, or a reload,
      // picks it up - and the composer stays usable either way.
    }
  }, [loadHistory]);

  useEffect(() => {
    setHistoryLoading(true);

    // Clear first. Otherwise the previous conversation's messages stay
    // on screen until the fetch returns - and the view anchors itself
    // to the bottom of the WRONG list on the way past.
    setHistory([]);
    setCursor(null);
    setHasMore(false);

    loadHistory().catch(() => setHistoryLoading(false));
  }, [loadHistory]);

  // Abort any in-flight turn if the user navigates away. Without this,
  // the fetch keeps running and its callbacks set state on an unmounted
  // component.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const send = useCallback(
    async (content: string) => {
      // BUSY MEANS RUNNING, NOT "SOMETHING IS ON SCREEN".
      //
      // This used to be `if (live) return`, and the chat page computes
      // busy as `live !== null && live.phase !== "idle"`. Those two
      // rules disagree about exactly one state - a finished-or-failed
      // turn still being displayed - and the disagreement is silent in
      // the worst possible way:
      //
      //     the page      composer enabled, Send button live
      //     this function returns immediately, sends nothing
      //
      // So after a turn that ended in an error, the user types, presses
      // Send, and the message simply vanishes. No request, no error, no
      // feedback. Reported as "the chat below my last message
      // disappears", and the only cure was a page reload.
      //
      // One definition of busy now, matching the one the UI paints.
      if (!content.trim() || (live && live.phase !== "idle")) return;

      const controller = new AbortController();
      abortRef.current = controller;

      setLive({
        phase: "routing",
        question: content,
        answer: "",
        timeline: [],
        approvals: [],
        approvalRequests: [],
        startedAt: Date.now(),
        round: 0,
        tokens: 0,
      });

      try {
        await streamTurn(conversationId, content, {
          signal: controller.signal,
          onEvent: (event) => applyEvent(setLive, event),
        });
      } catch (error) {
        // A user-initiated abort is not an error worth showing.
        if (controller.signal.aborted) {
          setLive(null);
          return;
        }

        setLive((prev) =>
          prev
            ? {
                ...prev,
                phase: "idle",
                error:
                  error instanceof Error
                    ? error.message
                    : "The connection was lost.",
              }
            : prev,
        );

        // THE TURN MAY HAVE FINISHED ANYWAY.
        //
        // A lost stream says nothing about the turn behind it: the
        // server deliberately does NOT cancel a running turn when the
        // client disconnects (see the publisher() finally in
        // api/routers/stream.py), because it may have already created
        // a GitHub issue. So the answer can be sitting in the database
        // while this browser shows nothing.
        //
        // Refetching here is what turns "the reply never appeared"
        // into "the reply appeared a moment late".
        await refreshHistory();

        return;
      }

      // The turn is persisted, so the server is now the source of
      // truth. Refetch rather than trusting the events we assembled -
      // that is what makes a page refresh mid-conversation safe.
      //
      // Guarded: a failed refetch must not be able to leave `live` set
      // for ever, because that is the state the composer refuses to
      // send from.
      await refreshHistory();

      // AN `error` EVENT ARRIVES INSIDE A PERFECTLY SUCCESSFUL STREAM.
      //
      // The turn failing is not the request failing: the response is a
      // 200 text/event-stream, the server reports the problem as an
      // event, and then closes the stream normally. So this line is
      // reached in both cases - and clearing `live` unconditionally
      // threw the error away microseconds after it was rendered.
      //
      // From the report: a rate-limit message that flashed and
      // vanished, leaving a chat where the question had disappeared
      // too (the failed turn's transaction takes it with it) and
      // nothing said why.
      //
      // A turn carrying an error stays on screen. `phase` is already
      // "idle", so the composer is usable - see the guard in send.
      setLive((prev) => (prev?.error ? prev : null));

      // Other screens showed counts derived from this turn.
      // The meter must reflect the turn that just ran, or a user
      // watching it count down sees a number that is always one
      // behind - and stops trusting it exactly when it matters.
      queryClient.invalidateQueries({ queryKey: ["limits"] });
      queryClient.invalidateQueries({ queryKey: ["executions"] });
      queryClient.invalidateQueries({ queryKey: ["execution-stats"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    [conversationId, live, refreshHistory, queryClient],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setLive(null);
  }, []);

  return {
    history,
    historyLoading,
    live,
    send,
    cancel,
    reload: loadHistory,

    // Paging, for the scroll container that owns the viewport.
    hasMore,
    loadingOlder,
    loadOlder,
  };
}

/**
 * Fold one SSE event into the live turn.
 *
 * Pure with respect to the event: given the same previous state and the
 * same event it always produces the same next state. That is what makes
 * a replayed or duplicated event harmless.
 */
function applyEvent(
  setLive: React.Dispatch<React.SetStateAction<LiveTurn | null>>,
  { event, data }: StreamEvent,
): void {
  setLive((prev) => {
    if (!prev) return prev;

    switch (event) {
      case "routing":
        return {
          ...prev,
          phase: "thinking",
          routing: {
            services: (data.services as string[]) ?? [],
            tools: (data.tools as number) ?? 0,
            confidence: (data.confidence as number) ?? 0,
          },
        };

      case "round_start":
        return {
          ...prev,
          phase: "thinking",
          round: (data.round as number) ?? prev.round + 1,

          // Cleared deliberately: the previous round's activity is
          // finished, and leaving "Reading github_get_file" on screen
          // while the model thinks about what it read is a small lie
          // that makes the next tool call look slower than it is.
          activity: undefined,
        };

      // Token counts arrive once per round and are pure progress: they
      // move while the model is thinking, which is exactly the stretch
      // where nothing else does.
      case "tokens":
        return {
          ...prev,
          tokens: (data.turn_total as number) ?? prev.tokens,
        };

      // The agent went looking for tools it was not given. The count
      // in the routing strip is what the model can currently reach, so
      // it has to grow when the tool set does - otherwise the header
      // says "8 tools" while the agent is calling a ninth.
      case "tool_search": {
        const added = (data.added as number) ?? 0;

        const withActivity = {
          ...prev,
          activity: "Looking for more tools",
        };

        if (!prev.routing || added === 0) return withActivity;

        return {
          ...withActivity,
          routing: {
            ...prev.routing,
            tools: prev.routing.tools + added,
          },
        };
      }

      case "tool_start": {
        const toolName = String(data.tool ?? "unknown");

        return {
          ...prev,
          phase: "tools",

          // The single most reassuring thing on screen: the NAME of
          // the thing being done right now. "Thinking..." for ninety
          // seconds reads as broken; "Reading github_get_file" for
          // ninety seconds reads as slow, which is the truth.
          activity: prettyToolActivity(toolName),

          timeline: [
            ...prev.timeline,
            {
              // tool_start carries no execution_id - the executor has
              // not created the record yet - so the key is synthetic
              // and only has to be unique within this turn.
              key: `pending-${toolName}-${prev.timeline.length}`,
              tool_name: toolName,
              pending: true,
            },
          ],
        };
      }

      case "tool_end": {
        const execution = data as unknown as ExecutionRead;

        // Already resolved? Then this is a duplicate event and there is
        // nothing to do. Keyed by the real execution id, so a replay
        // cannot add a second row.
        if (prev.timeline.some((row) => row.execution?.id === execution.id)) {
          return prev;
        }

        // Resolve the OLDEST still-pending row for this tool. Tools run
        // one at a time inside the loop - it awaits each execute() -
        // so first-pending is always the one that just finished, even
        // when the same tool is called twice in a round.
        const index = prev.timeline.findIndex(
          (row) => row.pending && row.tool_name === execution.tool_name,
        );

        const resolved: TimelineRow = {
          key: execution.id,
          tool_name: execution.tool_name,
          pending: false,
          execution,
        };

        const timeline =
          index === -1
            ? // No pending row: the tool_start was missed (a dropped
              // frame, or a reconnect). Append rather than discard -
              // losing a row the user can see is worse than an
              // out-of-order one.
              [...prev.timeline, resolved]
            : prev.timeline.map((row, i) => (i === index ? resolved : row));

        return { ...prev, phase: "thinking", timeline };
      }

      case "approval_required": {
        // The turn has stopped. Nothing else will arrive on this
        // stream until the approval is resolved or its timer runs
        // out - which is exactly why the card carries its own
        // countdown rather than a spinner.
        const event = data as unknown as PendingApprovalEvent;

        // Guard against a replayed frame adding the same question
        // twice. Same reasoning as the tool_end duplicate check.
        if (
          prev.approvalRequests.some(
            (entry) => entry.event.approval_id === event.approval_id,
          )
        ) {
          return prev;
        }

        return {
          ...prev,
          phase: "waiting",
          approvalRequests: [
            ...prev.approvalRequests,
            { event, status: null },
          ],
        };
      }

      case "approval_resolved": {
        // Fires for approve, deny AND expiry. The answer is RECORDED
        // on the question rather than clearing it, so the user can see
        // what they chose - a resolution from another tab, or from the
        // clock, lands here too and is shown the same way.
        const id = String(data.approval_id ?? "");
        const status = String(data.status ?? "denied") as ApprovalStatus;

        return {
          ...prev,
          phase: "thinking",
          approvalRequests: prev.approvalRequests.map((entry) =>
            entry.event.approval_id === id
              ? { ...entry, status }
              : entry,
          ),
        };
      }

      case "answer_ready":
        return {
          ...prev,
          phase: "writing",
          answer: String(data.answer ?? ""),
        };

      case "done":
        return {
          ...prev,
          phase: "idle",
          answer: String(data.answer ?? prev.answer),

          // The final event is the whole persisted ChatResponse, so
          // this is the first and only place the refused calls appear.
          // Only REFUSALS are in it - an approved call is not
          // something the agent "needed permission for".
          approvals: (data.approvals_required as ApprovalRequired[]) ?? [],

          // Nothing is waiting any more. A question still showing no
          // answer at this point was never resolved, so it is marked
          // expired rather than left spinning a countdown forever.
          approvalRequests: prev.approvalRequests.map((entry) =>
            entry.status === null
              ? { ...entry, status: "expired" as ApprovalStatus }
              : entry,
          ),
        };

      case "error": {
        // A rate-limited turn is not a failure - nothing broke, the
        // user has simply used their allowance. Saying "the agent turn
        // failed" would send them looking for a bug that is not there.
        const limited = data.code === "rate_limited";

        return {
          ...prev,
          phase: "idle",
          error: String(
            data.detail ??
              (limited
                ? "You have reached your limit for now."
                : "The agent turn failed."),
          ),
          rateLimited: limited,
        };
      }

      default:
        // An event this build does not know about - a newer backend,
        // most likely. Ignoring it is correct; throwing would break the
        // whole turn over something purely informational.
        return prev;
    }
  });
}


/**
 * The order messages are READ in.
 *
 * A plain reverse of the API's newest-first list is not enough, and the
 * reason is worth knowing.
 *
 * Every message column is stamped by PostgreSQL with `now()`. In
 * PostgreSQL `now()` is NOT the wall clock - it is the time the
 * TRANSACTION started, and it returns the same value for every row
 * written inside that transaction. One agent turn writes the user's
 * message and the assistant's reply in a single transaction, so the
 * pair lands with a byte-identical `created_at`:
 *
 *     user       2026-08-20T08:37:53.873322+00:00  "give me the auth data"
 *     assistant  2026-08-20T08:37:53.873322+00:00  "Here are the details..."
 *
 * With the timestamps tied, the only tiebreak left is the id - and the
 * id is a random UUID. So the answer had a coin-flip chance of being
 * rendered ABOVE the question that produced it, which is exactly what
 * a user reported seeing.
 *
 * Sorting by role inside a tie is not a guess. Within one turn the true
 * order is fixed and known: the person asks, tools run, the agent
 * replies. Encoding that makes the display correct and stable.
 *
 * A different timestamp always wins, so this only ever settles a tie
 * within a single turn - two separate turns can never share one
 * transaction.
 *
 * The deeper fix belongs in the backend: `clock_timestamp()` instead of
 * `now()`, or an explicit ordering column. Until then this keeps the
 * transcript honest.
 */
const ROLE_ORDER: Record<string, number> = {
  system: 0,
  user: 1,
  tool: 2,
  assistant: 3,
};

/**
 * A tool name, as a sentence about what is happening.
 *
 * `github_list_repositories` is a symbol; "Reading github repositories"
 * is a status. The verb comes from the tool's own first word, which is
 * the same convention agent/classification.py reads it by - so a tool
 * added tomorrow is described correctly with no table to update.
 */
function prettyToolActivity(toolName: string): string {
  const parts = toolName.split("_");
  const verb = parts[1] ?? "";
  const subject = parts.slice(2).join(" ") || parts[0];

  const READING = ["get", "list", "search", "read", "fetch", "download"];

  const lead = READING.includes(verb)
    ? "Reading"
    : verb
      ? verb.charAt(0).toUpperCase() + verb.slice(1)
      : "Calling";

  return `${lead} ${subject}`.trim();
}

function byConversationOrder(a: MessageRead, b: MessageRead): number {
  const timeA = new Date(a.created_at).getTime();
  const timeB = new Date(b.created_at).getTime();

  if (timeA !== timeB) return timeA - timeB;

  const roleA = ROLE_ORDER[a.role] ?? 99;
  const roleB = ROLE_ORDER[b.role] ?? 99;

  if (roleA !== roleB) return roleA - roleB;

  // Same instant AND same role: fall back to the id so the order is at
  // least stable between renders rather than shifting on each sort.
  return a.id.localeCompare(b.id);
}
