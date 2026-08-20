"use client";

import { useState } from "react";

import { RISK_CLASS, RISK_LABEL, formatDuration, prettyToolName, safeRisk } from "@/lib/tools";
import type { ExecutionRead } from "@/lib/types";
import type { TimelineRow } from "@/lib/hooks/use-chat";

/**
 * What the agent actually did, step by step.
 *
 * Phase 4's second milestone test is that a NON-TECHNICAL person can
 * look at this and say what happened. That is why rows read
 * "Searched GitHub - 312ms" rather than dumping a JSON payload, and why
 * the detail is behind a click instead of always on screen.
 *
 * The three statuses stay visually distinct on purpose:
 *
 *   success   it worked
 *   failed    something went wrong
 *   denied    the system correctly refused
 *
 * Merging denied into failed would make the product report a bug every
 * time its own safety rules did their job.
 */
export function ToolTimeline({
  rows,
  totalMs,
}: {
  rows: TimelineRow[];
  totalMs?: number;
}) {
  if (rows.length === 0) return null;

  const finished = rows.filter((r) => !r.pending).length;

  return (
    <div className="my-3 overflow-hidden rounded-lg border bg-muted/30">
      <div className="flex items-center justify-between border-b bg-muted/50 px-3 py-1.5">
        {/* Two SIBLING spans, not a nested one. Nested, the outer
            element's text content is "Tools1 of 1", so nothing on the
            page has the accessible text "Tools" - which breaks both
            testing and anyone navigating by text. */}
        <span className="flex items-baseline gap-2 text-xs">
          <span className="font-medium">Tools</span>
          <span className="text-muted-foreground">
            {finished} of {rows.length}
          </span>
        </span>

        {typeof totalMs === "number" && totalMs > 0 && (
          <span className="font-mono text-xs text-muted-foreground tabular-nums">
            {formatDuration(totalMs)}
          </span>
        )}
      </div>

      <ul className="divide-y">
        {rows.map((row) => (
          <TimelineItem key={row.key} row={row} />
        ))}
      </ul>
    </div>
  );
}

function TimelineItem({ row }: { row: TimelineRow }) {
  const [open, setOpen] = useState(false);

  if (row.pending) {
    return (
      <li className="flex items-center gap-2 px-3 py-2 text-sm">
        <span
          aria-hidden
          className="size-3 shrink-0 animate-pulse rounded-full bg-sky-500"
        />
        <span className="min-w-0 truncate">
          Running {prettyToolName(row.tool_name)}...
        </span>
      </li>
    );
  }

  const execution = row.execution;
  if (!execution) return null;

  const tone = statusTone(execution.status);

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        // aria-expanded is what tells a screen reader this is a
        // disclosure and whether it is currently open.
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50"
      >
        <span aria-hidden className={`shrink-0 font-mono ${tone.className}`}>
          {tone.glyph}
        </span>

        <span className="min-w-0 flex-1 truncate">
          {prettyToolName(execution.tool_name, execution.namespace)}
        </span>

        {/* The status WORD, not only a colour and a symbol. */}
        <span className={`shrink-0 text-xs ${tone.className}`}>
          {tone.label}
        </span>

        <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
          {formatDuration(execution.duration_ms)}
        </span>
      </button>

      {open && <ExecutionDetail execution={execution} />}
    </li>
  );
}

function ExecutionDetail({ execution }: { execution: ExecutionRead }) {
  const risk = safeRisk(execution.risk_level);

  // The executor already attaches a recovery hint from RECOVERY_HINTS
  // in agent/errors.py. Written once in Phase 2, shown here for free.
  const hint =
    (execution.error?.recovery_hint as string | undefined) ??
    (execution.error?.message as string | undefined);

  return (
    <div className="space-y-3 border-t bg-background/60 px-3 py-3 text-xs">
      {hint && (
        <p className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {hint}
        </p>
      )}

      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
        <Field label="Service" value={execution.namespace ?? "unknown"} />
        <Field label="Operation" value={execution.operation} />

        <dt className="text-muted-foreground">Risk</dt>
        <dd>
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${RISK_CLASS[risk]}`}
          >
            {RISK_LABEL[risk]}
          </span>
        </dd>

        {execution.attempts > 1 && (
          <Field label="Attempts" value={String(execution.attempts)} />
        )}

        <Field label="Execution" value={execution.id} mono />
      </dl>
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "truncate font-mono" : "truncate"}>{value}</dd>
    </>
  );
}

function statusTone(status: string) {
  switch (status) {
    case "success":
      return {
        glyph: "✓",
        label: "Worked",
        className: "text-emerald-600 dark:text-emerald-400",
      };

    case "denied":
      // Not red. Nothing went wrong - the agent asked for something it
      // was not allowed to do and the rules said no.
      return {
        glyph: "⊘",
        label: "Not allowed",
        className: "text-amber-600 dark:text-amber-400",
      };

    default:
      return {
        glyph: "✕",
        label: "Failed",
        className: "text-red-600 dark:text-red-400",
      };
  }
}
