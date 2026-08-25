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
}

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

  // Held in a ref, not state: aborting is an imperative action and must
  // not cause a re-render, and the value must survive between renders.
  const abortRef = useRef<AbortController | null>(null);

  const loadHistory = useCallback(async () => {
    const page = await listMessages(conversationId, { limit: 50 });

    // The API returns newest-first (that is what makes cursor paging
    // work when scrolling up). A chat reads oldest-first, so sort a
    // COPY - .sort() and .reverse() both mutate, and mutating a cached
    // array would corrupt whatever else is holding it.
    setHistory([...page.items].sort(byConversationOrder));
    setHistoryLoading(false);
  }, [conversationId]);

  useEffect(() => {
    setHistoryLoading(true);
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
      if (!content.trim() || live) return;

      const controller = new AbortController();
      abortRef.current = controller;

      setLive({
        phase: "routing",
        question: content,
        answer: "",
        timeline: [],
        approvals: [],
        approvalRequests: [],
      });

      try {
        await streamTurn(conversationId, content, {
          signal: controller.signal,
          onEvent: (event) => applyEvent(setLive, event),
        });
      } catch (error) {
        // A user-initiated abort is not an error worth showing.
        if (controller.signal.aborted) return;

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

        return;
      }

      // The turn is persisted, so the server is now the source of
      // truth. Refetch rather than trusting the events we assembled -
      // that is what makes a page refresh mid-conversation safe.
      await loadHistory();
      setLive(null);

      // Other screens showed counts derived from this turn.
      // The meter must reflect the turn that just ran, or a user
      // watching it count down sees a number that is always one
      // behind - and stops trusting it exactly when it matters.
      queryClient.invalidateQueries({ queryKey: ["limits"] });
      queryClient.invalidateQueries({ queryKey: ["executions"] });
      queryClient.invalidateQueries({ queryKey: ["execution-stats"] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    [conversationId, live, loadHistory, queryClient],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setLive(null);
  }, []);

  return { history, historyLoading, live, send, cancel, reload: loadHistory };
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
        return { ...prev, phase: "thinking" };

      // The agent went looking for tools it was not given. The count
      // in the routing strip is what the model can currently reach, so
      // it has to grow when the tool set does - otherwise the header
      // says "8 tools" while the agent is calling a ninth.
      case "tool_search": {
        const added = (data.added as number) ?? 0;

        if (!prev.routing || added === 0) return prev;

        return {
          ...prev,
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
