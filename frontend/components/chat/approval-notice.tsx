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
import type { ExecutionRead } from "@/lib/types";

/**
 * The shape the notice renders.
 *
 * ONE SOURCE: executions with status `denied`, whether they arrived a
 * moment ago on the live stream or were read back from the database an
 * hour later. The live turn used to render from `approvals_required`
 * instead, so a turn could describe itself one way on screen and a
 * different way after a refresh - and only one of the two could ever
 * see a scope refusal, because a scope refusal never reaches the
 * approval list at all.
 *
 * `approvals_required` is still on the API for clients that cannot
 * stream; this component simply no longer needs it.
 */
export interface RefusedCall {
  tool_name: string;
  operation: string;
  risk_level: string;
  argument_keys?: string[];

  /**
   * WHY it was refused. Four answers, four different next steps:
   *
   *   permission   the agent was never allowed to do this. Fixed on
   *                the permissions screen.
   *
   *   denied       a person read the arguments and said no. Nothing
   *                to fix - this is the system working.
   *
   *   expired      the prompt was shown and nobody answered in time.
   *                Fixed by asking again.
   *
   *   unavailable  the agent IS allowed, but there was nobody to ask
   *                - a script, the API, a scheduled run. Fixed by
   *                asking again in the chat.
   *
   * Merging any two of these produces a message that is actively
   * false. Telling someone "there was nobody to answer" about a
   * request they just clicked Deny on reads as a bug in software that
   * did exactly what they asked.
   */
  reason?: RefusalReason;
}

export type RefusalReason =
  | "permission"
  | "denied"
  | "expired"
  | "unavailable";

/**
 * The refusal reason behind one stored execution.
 *
 * READS `type`, NOT `code`.
 *
 * ToolError.to_dict() has always serialised the error code under
 * `type` (agent/errors.py). This function read `code`, got `undefined`
 * every single time, and fell through to its default - so EVERY
 * refusal in a reloaded conversation was reported as "nobody was
 * there to confirm", including scope refusals that never reached a
 * human and denials the user had made themselves.
 *
 * The bug was invisible because the fallback is a plausible sentence.
 */
function reasonFromError(error: unknown): RefusalReason {
  const type = (error as { type?: string } | null)?.type;

  switch (type) {
    case "permission_denied":
      return "permission";
    case "approval_denied":
      return "denied";
    case "approval_expired":
      return "expired";
    default:
      return "unavailable";
  }
}

/** Denied executions on a stored message, as refused calls. */
export function refusedFromExecutions(
  executions: (ExecutionRead | undefined)[] | undefined,
): RefusedCall[] {
  return (executions ?? [])
    .filter(
      (execution): execution is ExecutionRead =>
        execution !== undefined && execution.status === "denied",
    )
    .map((execution) => ({
      tool_name: execution.tool_name,
      operation: execution.operation,
      risk_level: execution.risk_level,
      reason: reasonFromError(execution.error),
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

  // ONE headline for a mixed list, chosen by which refusal most needs
  // acting on. A permission refusal wins because it is the only one
  // the user must go somewhere else to fix; a denial ranks last
  // because it needs no action at all - it is the system working.
  const reason = mostActionable(approvals);

  const count = approvals.length;
  const plural = count === 1 ? "" : "s";

  return (
    <div
      data-testid="approval-notice"
      data-reason={reason}
      className="my-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950"
    >
      <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
        {reason === "denied"
          ? `You declined ${count} action${plural}`
          : reason === "expired"
            ? `${count} request${plural} timed out`
            : `Your agent needed permission for ${count} action${plural}`}
      </p>

      <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
        {reason === "permission" ? (
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
        ) : reason === "denied" ? (
          // No "fix this" link. Nothing is broken - the user was asked,
          // read the arguments and said no, which is the feature.
          "Nothing was changed in your accounts."
        ) : reason === "expired" ? (
          "Nothing was changed in your accounts. The confirmation was "
          + "not answered in time - ask again and it will wait for you."
        ) : (
          "Nothing was changed in your accounts. These tools always ask "
          + "first, and there was nobody to ask."
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
              {detail?.reason === "denied"
                ? "You declined this"
                : detail?.reason === "expired"
                  ? "This request timed out"
                  : "Permission needed"}
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
                {DETAIL_ADVICE[detail.reason ?? "unavailable"]}
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

/**
 * Which refusal in a mixed list decides the headline.
 *
 * Ordered by how much the user has to DO about it:
 *
 *   permission   go to another screen and grant something
 *   unavailable  ask again somewhere a prompt can appear
 *   expired      ask again, and answer this time
 *   denied       nothing; this is the system working
 *
 * A list holding both a scope refusal and a denial is reported as the
 * scope refusal, because that is the one still blocking them.
 */
const REASON_RANK: RefusalReason[] = [
  "permission",
  "unavailable",
  "expired",
  "denied",
];

function mostActionable(approvals: RefusedCall[]): RefusalReason {
  for (const reason of REASON_RANK) {
    if (approvals.some((approval) => approval.reason === reason)) {
      return reason;
    }
  }

  return "unavailable";
}

const DETAIL_ADVICE: Record<RefusalReason, string> = {
  permission:
    "Nothing in your account was changed. This agent has not been "
    + "allowed to do this kind of action. Grant the matching permission "
    + "on its permissions screen, then ask again.",

  denied:
    "Nothing in your account was changed. You were shown this and "
    + "declined it. Ask the agent again if you have changed your mind.",

  expired:
    "Nothing in your account was changed. You were asked to confirm "
    + "this and the request timed out before it was answered. Ask again "
    + "and the agent will wait for you.",

  unavailable:
    "Nothing in your account was changed. This was asked outside a "
    + "live chat - from the API, a script, or a scheduled run - where "
    + "there was nobody to answer. Ask the agent again in the chat and "
    + "it will stop and wait for you.",
};
