"use client";

import { useState } from "react";

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
  }));
}

/**
 * "Your agent wanted to do something and was stopped."
 *
 * WHAT THIS IS, AND WHAT IT IS NOT
 *
 * Phase 4.7 describes a dialog with Approve and Cancel buttons, where
 * clicking Approve lets the tool run. This is NOT that dialog, because
 * the backend cannot do that yet and pretending otherwise would be the
 * worst possible outcome.
 *
 * Today, api/approvals.py DENIES any call that needs confirmation. It
 * records the refusal as `denied` (not `failed` - nothing broke) and
 * reports it in `approvals_required`. That was a deliberate choice, and
 * the reasoning in that file is worth reading: auto-approving would be
 * one line and the wrong line, because the approval flag would still
 * LOOK enabled in the UI while doing nothing.
 *
 * Making Approve actually execute needs a suspendable agent turn -
 * persist the pending call, return, and re-enter the loop when the user
 * clicks. That is Phase 5.
 *
 * So this component tells the truth: it explains what was blocked, why,
 * and what the user can do about it right now. A button that lies about
 * what it does is worse than no button.
 *
 * ON ARGUMENTS
 *
 * Phase 4.7 rightly insists on showing the ACTUAL arguments - "Approve
 * this action?" with no detail trains people to click Approve blindly.
 * The backend currently sends `argument_keys` only: the NAMES the call
 * would have used, not the values. So the names are what is shown, and
 * the gap is stated plainly rather than papered over.
 */
export function ApprovalNotice({
  approvals,
}: {
  approvals: RefusedCall[];
}) {
  const [detail, setDetail] = useState<RefusedCall | null>(null);

  if (approvals.length === 0) return null;

  return (
    <div className="my-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950">
      <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
        {approvals.length === 1
          ? "Your agent needed permission for 1 action"
          : `Your agent needed permission for ${approvals.length} actions`}
      </p>

      <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
        Nothing was changed in your accounts. These tools always ask
        first.
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
                Approving an action from here is not available yet.
                Nothing in your account was changed. If you want this
                agent to be able to do this without asking, you can turn
                the tool on in its settings - but think about it first,
                because it will then run without stopping to check.
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
