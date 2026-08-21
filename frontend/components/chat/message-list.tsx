"use client";

import { useEffect, useRef } from "react";

import {
  ApprovalNotice,
  refusedFromApprovals,
  refusedFromExecutions,
} from "./approval-notice";
import { ApprovalRequest } from "@/components/approvals/approval-request";
import { Markdown } from "./markdown";
import { ToolTimeline } from "./tool-timeline";
import type { LiveTurn } from "@/lib/hooks/use-chat";
import type { MessageRead } from "@/lib/types";

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
}: {
  history: MessageRead[];
  live: LiveTurn | null;

  // Only so a refusal can link to the screen that fixes it. The list
  // itself does not care which agent is talking.
  agentId?: string;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Follow the conversation as it grows. Depending on the phase as well
  // as the counts means the view also follows tool rows appearing, not
  // just whole messages.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [history.length, live?.timeline.length, live?.phase, live?.answer]);

  // role='tool' rows are stored for the audit trail but are noise in a
  // transcript - the assistant's own summary already says what
  // happened, and the timeline shows the mechanics.
  const visible = history.filter(
    (message) => message.role === "user" || message.role === "assistant",
  );

  return (
    <div className="space-y-6">
      {visible.map((message) => (
        <Turn key={message.id} role={message.role}>
          <Bubble role={message.role}>{message.content}</Bubble>

          {message.role === "assistant" && (
            <ApprovalNotice
              approvals={refusedFromExecutions(message.executions)}
              agentId={agentId}
            />
          )}

          {message.role === "assistant" &&
            (message.executions ?? []).length > 0 && (
              <ToolTimeline
                rows={(message.executions ?? []).map((execution) => ({
                  key: execution.id,
                  tool_name: execution.tool_name,
                  pending: false,
                  execution,
                }))}
                totalMs={(message.executions ?? []).reduce(
                  (sum, e) => sum + e.duration_ms,
                  0,
                )}
              />
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

            {/* The turn is parked on this one. Rendered BELOW the
                timeline so the sequence reads in the order it
                happened: here is what I have done, here is what I
                want to do next. */}
            {live.pendingApproval && (
              <ApprovalRequest approval={live.pendingApproval} />
            )}

            <ApprovalNotice
              approvals={refusedFromApprovals(live.approvals)}
              agentId={agentId}
            />

            {live.answer ? (
              <Bubble role="assistant">{live.answer}</Bubble>
            ) : (
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
function StatusLine({ live }: { live: LiveTurn }) {
  const text = (() => {
    switch (live.phase) {
      case "routing":
        return "Working out which tools to use...";

      case "tools": {
        const service = live.routing?.services?.[0];
        return service ? `Searching ${service}...` : "Using tools...";
      }

      case "waiting":
        // Never "Thinking...". The agent is not thinking, it is
        // stopped and waiting for this person - and telling them it is
        // busy is how a request sits unanswered until it expires.
        return "Waiting for your approval...";

      case "writing":
        return "Writing the answer...";

      default:
        return "Thinking...";
    }
  })();

  return (
    // aria-live so a screen reader is told about each change without
    // the user having to go looking for it.
    <p aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
      <span
        aria-hidden
        className="size-2 animate-pulse rounded-full bg-foreground/50"
      />
      {text}
    </p>
  );
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
