"use client";

import { useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  OPERATION_HELP,
  RISK_CLASS,
  RISK_LABEL,
  prettyToolName,
  safeOperation,
  safeRisk,
} from "@/lib/tools";
import type { ApprovalRequired, ExecutionRead } from "@/lib/types";

/**
 * The shape the notice renders, from either of two sources.
 *
 * LIVE turn     `approvals_required` on the final `done` event. Carries
 *               `argument_keys`, but exists only while the turn is on
 *               screen.
 *
 * HISTORY       executions stored with status `denied`. No argument
 *               names, but it is in the database - so it survives a
 *               reload, which is what Phase 4's milestone 5 requires.
 *
 * Normalising both into one shape is why the notice can be rendered
 * from whichever source a given message has.
 */
export interface RefusedCall {
  tool_name: string;
  operation: string;
  risk_level: string;
  argument_keys?: string[];

  /**
   * WHY it was refused, and the two answers need different advice.
   *
   *   permission  the agent was never allowed to do this. The user
   *               fixes it on the permissions screen.
   *
   *   approval    the agent IS allowed, but nobody was there to
   *               confirm. The user fixes it by asking again in the
   *               chat, where the prompt can appear.
   *
   * Merging them produces the worst possible message: "this needs your
   * permission" shown to someone who has already granted it, with no
   * hint of where to look.
   */
  reason?: "permission" | "approval";
}

/** Denied executions on a stored message, as refused calls. */
export function refusedFromExecutions(
  executions: ExecutionRead[] | undefined,
): RefusedCall[] {
  return (executions ?? [])
    .filter((execution) => execution.status === "denied")
    .map((execution) => ({
      tool_name: execution.tool_name,
      operation: execution.operation,
      risk_level: execution.risk_level,
      reason:
        (execution.error as { code?: string } | null)?.code ===
        "permission_denied"
          ? ("permission" as const)
          : ("approval" as const),
    }));
}

/** `approvals_required` from a live turn, as refused calls. */
export function refusedFromApprovals(
  approvals: ApprovalRequired[],
): RefusedCall[] {
  return approvals.map((approval) => ({
    tool_name: approval.tool_name,
    operation: approval.operation,
    risk_level: approval.risk_level,
    argument_keys: approval.argument_keys ?? undefined,

    // This list only ever holds calls that needed a human. A scope
    // refusal never reaches it - it is denied before anyone is asked.
    reason: "approval" as const,
  }));
}

/**
 * "Your agent wanted to do something and was stopped."
 *
 * WHAT THIS IS, AND WHAT IT IS NOT
 *
 * This is the AFTERMATH view: calls that were refused because nobody
 * could be asked. It is not the approval prompt - that is
 * components/approvals/approval-request.tsx, which appears live in the
 * chat while the turn is suspended, shows the real argument VALUES and
 * has working Approve and Deny buttons.
 *
 * Both exist because the backend has two approval handlers, and which
 * one runs depends on whether the caller can wait (api/approvals.py):
 *
 *   WebApproval        the streaming chat endpoint. Suspends the turn
 *                      and asks. -> approval-request.tsx
 *
 *   DeferredApproval   a plain POST, a CLI, a scheduled job. Nobody is
 *                      watching, so it denies and reports. -> here
 *
 * It is also what a STORED turn shows: executions with status
 * `denied`, read back from the database long after the dialog is gone.
 *
 * ON ARGUMENTS
 *
 * This view has `argument_keys` only - the NAMES a refused call would
 * have used, never the values. That is correct here: nobody is being
 * asked to make a decision, so the values would be shown to no one and
 * logged to everyone. The live prompt is the opposite, and for the
 * same reason - there, the values ARE the decision.
 */
export function ApprovalNotice({
  approvals,
  agentId,
}: {
  approvals: RefusedCall[];
  agentId?: string;
}) {
  const [detail, setDetail] = useState<RefusedCall | null>(null);

  if (approvals.length === 0) return null;

  // If ANY of them was a permission refusal, the permission advice is
  // the one that unblocks the user - so it wins.
  const blocked = approvals.some((a) => a.reason === "permission");

  return (
    <div className="my-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950">
      <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
        {approvals.length === 1
          ? "Your agent needed permission for 1 action"
          : `Your agent needed permission for ${approvals.length} actions`}
      </p>

      <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
        {blocked ? (
          <>
            Nothing was changed in your accounts. This agent has not been
            allowed to do this
            {agentId ? (
              <>
                {" - "}
                <Link
                  href={`/agents/${agentId}/permissions`}
                  className="underline"
                >
                  review its permissions
                </Link>
              </>
            ) : null}
            .
          </>
        ) : (
          "Nothing was changed in your accounts. These tools always ask first."
        )}
      </p>

      <ul className="mt-3 space-y-1.5">
        {approvals.map((approval) => {
          const risk = safeRisk(approval.risk_level);

          return (
            <li
              key={approval.tool_name}
              className="flex flex-wrap items-center gap-2"
            >
              <span aria-hidden className="font-mono text-amber-700 dark:text-amber-400">
                ⊘
              </span>

              <span className="text-sm font-medium">
                {prettyToolName(approval.tool_name)}
              </span>

              <span
                className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${RISK_CLASS[risk]}`}
              >
                {RISK_LABEL[risk]}
              </span>

              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-xs"
                onClick={() => setDetail(approval)}
              >
                Details
              </Button>
            </li>
          );
        })}
      </ul>

      <Dialog
        open={Boolean(detail)}
        onOpenChange={(open) => !open && setDetail(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span aria-hidden>⚠</span>
              Permission needed
            </DialogTitle>

            <DialogDescription>
              {detail && (
                <>
                  Your agent tried to use{" "}
                  <strong>{prettyToolName(detail.tool_name)}</strong> and
                  was stopped.
                </>
              )}
            </DialogDescription>
          </DialogHeader>

          {detail && (
            <div className="space-y-4 text-sm">
              <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2">
                <dt className="text-muted-foreground">Tool</dt>
                <dd className="font-mono text-xs">{detail.tool_name}</dd>

                <dt className="text-muted-foreground">What it does</dt>
                <dd>{OPERATION_HELP[safeOperation(detail.operation)]}</dd>

                <dt className="text-muted-foreground">Risk</dt>
                <dd>
                  <span
                    className={`rounded border px-1.5 py-0.5 text-xs font-medium ${
                      RISK_CLASS[safeRisk(detail.risk_level)]
                    }`}
                  >
                    {RISK_LABEL[safeRisk(detail.risk_level)]}
                  </span>
                </dd>

                {(detail.argument_keys ?? []).length > 0 && (
                  <>
                    <dt className="text-muted-foreground">Would have set</dt>
                    <dd className="font-mono text-xs">
                      {(detail.argument_keys ?? []).join(", ")}
                    </dd>
                  </>
                )}
              </dl>

              <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
                Nothing in your account was changed. This was asked
                outside a live chat - from the API, a script, or a
                scheduled run - where there was nobody to answer. Ask
                the agent again in the chat and it will stop and wait
                for you.
              </p>
            </div>
          )}

          <DialogFooter>
            <Button variant="ghost" onClick={() => setDetail(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
