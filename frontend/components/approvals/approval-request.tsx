"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { approveCall, denyCall } from "@/lib/api/approvals";
import { ApiError } from "@/lib/api/client";
import {
  OPERATION_HELP,
  RISK_LABEL,
  safeOperation,
  safeRisk,
} from "@/lib/tools";
import type { ApprovalStatus } from "@/lib/types";

/**
 * The approval prompt.
 *
 * WHAT IS ACTUALLY HAPPENING WHILE THIS IS ON SCREEN
 *
 * The agent turn is SUSPENDED on the server. It is parked inside the
 * executor, awaiting an asyncio.Event, holding no database connection
 * and no MCP lock (api/approvals.py explains why). Clicking a button
 * here resolves a row in `pending_approvals`, which wakes that turn and
 * lets it carry on. Doing nothing denies it when the timer runs out.
 *
 * So this is not a notification about something that already happened.
 * It is a question, and something is waiting for the answer.
 *
 * WHY IT LOOKS LIKE A TERMINAL
 *
 * Because it is a command being run on the user's behalf, and the
 * arguments are the whole point. A friendly card with "Approve this
 * action?" and a summary teaches people to click Approve without
 * reading. A monospace block showing
 *
 *     email:  attacker@evil.com
 *
 * does not. The values are aligned, verbatim and unavoidable, and the
 * keyboard shortcuts are the ones a developer already has in their
 * fingers.
 *
 * This is the last line of defence against prompt injection. Every
 * layer below it - routing, the tool switch, the scope, the OAuth
 * scope - is code that cannot be talked out of its decision. This one
 * is a human, and a human can only decide well if they can see.
 */

export interface PendingApprovalEvent {
  approval_id: string;
  tool: string;
  operation: string;
  risk_level: string;
  arguments: Record<string, unknown>;
  expires_at: string;
  timeout_seconds: number;
}

export function ApprovalRequest({
  approval,
  resolvedStatus = null,
  onResolved,
}: {
  approval: PendingApprovalEvent;

  /**
   * The answer, when it came from somewhere other than this card.
   *
   * A resolution can arrive three ways: this card's buttons, ANOTHER
   * TAB answering the same question, or the server's timer expiring
   * it. Only the first was ever visible here, because the outcome
   * lived in local state.
   *
   * Passing it in means the card shows what happened however it
   * happened - and, more importantly, means the parent no longer has
   * to unmount the card to represent "resolved". Unmounting is what
   * made Deny look like the prompt had simply vanished.
   */
  resolvedStatus?: ApprovalStatus | null;

  onResolved?: (status: ApprovalStatus) => void;
}) {
  const [answered, setAnswered] = useState<ApprovalStatus | null>(null);

  // Whichever arrived first. The local answer wins so the card reacts
  // the instant a button is pressed, without waiting for the server
  // round trip to come back through the event stream.
  const settled = answered ?? resolvedStatus;

  const remaining = useCountdown(approval.expires_at, settled === null);

  const risk = safeRisk(approval.risk_level);
  const operation = safeOperation(approval.operation);

  const resolve = useMutation({
    mutationFn: (approved: boolean) =>
      approved
        ? approveCall(approval.approval_id)
        : denyCall(approval.approval_id),

    onSuccess: (result) => {
      const status = result.status as ApprovalStatus;

      setAnswered(status);
      onResolved?.(status);
    },

    onError: (error) => {
      // 409 means somebody - or the clock - already answered. Not a
      // failure worth a red banner: the correct thing happened, this
      // tab was just not the one that did it.
      if (error instanceof ApiError && error.status === 409) {
        setAnswered("expired");
        onResolved?.("expired");
        toast.info("That request was already answered.");
        return;
      }

      toast.error("Could not send your answer. Try again.");
    },
  });

  const busy = resolve.isPending;
  const done = settled !== null;
  const expired = remaining <= 0 && !done;

  const answer = useCallback(
    (approved: boolean) => {
      if (busy || done || expired) return;
      resolve.mutate(approved);
    },
    [busy, done, expired, resolve],
  );

  /**
   * y / n, and Escape to deny.
   *
   * Escape denies rather than dismissing. There is nothing to dismiss:
   * a turn is waiting, and the safe answer to "I do not want to deal
   * with this" is no. Enter is deliberately NOT bound to Approve -
   * approving must be a decision, never a reflex on a key people press
   * to get rid of things.
   */
  useEffect(() => {
    if (done || expired) return;

    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;

      // Never steal a key from someone typing their next message.
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }

      const key = event.key.toLowerCase();

      if (key === "y") {
        event.preventDefault();
        answer(true);
      } else if (key === "n" || event.key === "Escape") {
        event.preventDefault();
        answer(false);
      }
    };

    window.addEventListener("keydown", onKey);

    return () => window.removeEventListener("keydown", onKey);
  }, [answer, done, expired]);

  const entries = useMemo(
    () => Object.entries(approval.arguments ?? {}),
    [approval.arguments],
  );

  const width = entries.reduce(
    (longest, [key]) => Math.max(longest, key.length),
    0,
  );

  return (
    <section
      // The prompt is the most important thing on the page when it is
      // present, and a screen reader must be told so - "assertive"
      // interrupts, which is correct for a question that expires.
      role="alertdialog"
      aria-live="assertive"
      aria-label={`Approval required for ${approval.tool}`}
      data-testid="approval-request"
      data-approval-id={approval.approval_id}
      className="my-3 overflow-hidden rounded-lg border border-amber-400/60 bg-zinc-950 font-mono text-[13px] text-zinc-100 shadow-lg dark:border-amber-500/40"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-zinc-800 bg-zinc-900/80 px-3 py-2">
        <span aria-hidden className="text-amber-400">
          ▌
        </span>

        <span className="font-medium text-zinc-100">
          {done || expired
            ? "Agent asked to run"
            : "Agent wants to run"}
        </span>

        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${RISK_BADGE[risk]}`}
        >
          {RISK_LABEL[risk]}
        </span>

        <span className="ml-auto tabular-nums text-xs">
          {done ? (
            <StatusTag status={settled} />
          ) : expired ? (
            <StatusTag status="expired" />
          ) : (
            <span
              className={
                remaining <= 30 ? "text-red-400" : "text-zinc-400"
              }
              // The countdown updates every second; announcing each
              // tick would make a screen reader unusable.
              aria-hidden
            >
              {formatClock(remaining)}
            </span>
          )}
        </span>
      </header>

      <div className="space-y-3 px-3 py-3">
        <p className="flex items-start gap-2">
          <span aria-hidden className="select-none text-emerald-400">
            $
          </span>
          <span className="break-all text-zinc-50">{approval.tool}</span>
        </p>

        {entries.length > 0 ? (
          <dl className="space-y-1 border-l-2 border-zinc-800 pl-3">
            {entries.map(([key, value]) => (
              <div key={key} className="flex flex-wrap gap-x-2">
                <dt className="shrink-0 text-zinc-500">
                  {/* Padded so the values line up. Alignment is not
                      decoration here - a column of values is scannable,
                      a ragged list is not, and this is the text the
                      whole decision rests on. */}
                  {key.padEnd(width, " ")}
                </dt>
                <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-zinc-100">
                  {formatValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="pl-3 text-zinc-500">(no arguments)</p>
        )}

        <p className="text-xs text-zinc-400">
          {OPERATION_HELP[operation]}
        </p>
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-zinc-800 bg-zinc-900/80 px-3 py-2">
        {done || expired ? (
          <p className="text-xs text-zinc-400">
            {settled === "approved"
              ? "Approved. The agent is continuing."
              : settled === "denied"
                ? "Denied. Nothing was changed in your accounts."
                : "This request expired. Nothing was changed."}
          </p>
        ) : (
          <>
            <span className="text-xs text-zinc-500">
              Press{" "}
              <kbd className="rounded border border-zinc-700 px-1">y</kbd>{" "}
              or{" "}
              <kbd className="rounded border border-zinc-700 px-1">n</kbd>
            </span>

            <div className="ml-auto flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => answer(false)}
                className="h-7 border border-zinc-700 px-3 text-xs text-zinc-200 hover:bg-zinc-800 hover:text-zinc-50"
              >
                Deny
              </Button>

              <Button
                type="button"
                size="sm"
                disabled={busy}
                onClick={() => answer(true)}
                className="h-7 bg-emerald-600 px-3 text-xs text-white hover:bg-emerald-500"
              >
                {busy ? "Sending..." : "Approve"}
              </Button>
            </div>
          </>
        )}
      </footer>
    </section>
  );
}

function StatusTag({ status }: { status: ApprovalStatus | null }) {
  const map: Record<string, string> = {
    approved: "text-emerald-400",
    denied: "text-zinc-400",
    expired: "text-amber-400",
  };

  const label: Record<string, string> = {
    approved: "approved",
    denied: "denied",
    expired: "expired",
  };

  const key = status ?? "expired";

  return <span className={map[key]}>{label[key]}</span>;
}

/**
 * Seconds left, ticking once a second.
 *
 * Derived from `expires_at` rather than counting down from
 * `timeout_seconds`, because the two disagree the moment anything is
 * slow: a page opened thirty seconds into a five-minute window must
 * show 4:30, not 5:00. The server's absolute deadline is the truth.
 */
function useCountdown(expiresAt: string, running: boolean): number {
  const deadline = useMemo(
    () => new Date(expiresAt).getTime(),
    [expiresAt],
  );

  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return;

    const id = window.setInterval(() => setNow(Date.now()), 1000);

    return () => window.clearInterval(id);
  }, [running]);

  return Math.max(0, Math.round((deadline - now) / 1000));
}

function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;

  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/**
 * One argument value, as text.
 *
 * Strings are shown VERBATIM - no truncation, no ellipsis. The backend
 * has already redacted secrets and clipped anything enormous
 * (agent/execution.py), so what arrives here is meant to be read. A UI
 * that shortens "attacker@evil.com" to "attacker@..." would defeat the
 * entire purpose of the screen.
 */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "-";

  if (typeof value === "string") return value;

  return JSON.stringify(value);
}

/** Dark-surface risk colours. The card has its own background. */
const RISK_BADGE: Record<string, string> = {
  safe: "bg-emerald-500/15 text-emerald-300",
  low: "bg-sky-500/15 text-sky-300",
  medium: "bg-amber-500/15 text-amber-300",
  high: "bg-orange-500/20 text-orange-300",
  critical: "bg-red-500/20 text-red-300",
};
