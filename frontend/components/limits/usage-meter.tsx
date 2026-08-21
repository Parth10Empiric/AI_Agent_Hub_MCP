"use client";

import { useQuery } from "@tanstack/react-query";

import { getLimits } from "@/lib/api/limits";
import type { LimitRead } from "@/lib/types";

/**
 * How much of each hourly budget is left.
 *
 * WHY THIS SCREEN EXISTS AT ALL
 *
 * A limit nobody can see arrives as a mystery: the agent worked all
 * morning and now it does not, with no way to tell whether something
 * is broken or whether you simply used it a lot. Showing the budget
 * shrinking turns that into information - and it turns the refusal,
 * when it comes, into something the user already saw approaching.
 *
 * The backend answers with peek(), never check(), so rendering this
 * costs nothing. It is safe to poll and safe to leave on screen.
 */
export function UsageMeter({
  agentId,
  compact = false,
}: {
  agentId?: string;
  compact?: boolean;
}) {
  const limits = useQuery({
    queryKey: ["limits", agentId ?? null],
    queryFn: () => getLimits(agentId),

    // A minute is short enough to feel live and long enough that an
    // idle tab is not a load generator - the irony of a usage meter
    // that becomes the reason you run out.
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  if (!limits.data?.enabled) return null;

  const rows = limits.data.limits ?? [];

  if (rows.length === 0) return null;

  if (compact) {
    // Only ever the tightest one. A row of four meters above a chat
    // box is noise; the one that is about to stop you is not.
    const worst = [...rows].sort(byPressure)[0];

    if (!worst || pressure(worst) < 0.7) return null;

    return (
      <p
        className="text-xs text-muted-foreground"
        data-testid="usage-compact"
      >
        {worst.remaining === 0 ? (
          <span className="text-amber-600 dark:text-amber-400">
            {worst.label.toLowerCase()} used up
            {worst.resets_in > 0 && ` - resets in ${minutes(worst.resets_in)}`}
          </span>
        ) : (
          <>
            {worst.remaining} {worst.label.toLowerCase()} left this hour
          </>
        )}
      </p>
    );
  }

  return (
    <section className="space-y-3" data-testid="usage-meter">
      <div>
        <h2 className="font-semibold">This hour</h2>
        <p className="text-sm text-muted-foreground">
          Limits reset gradually, not on the hour - each action stops
          counting sixty minutes after it happened.
        </p>
      </div>

      <ul className="divide-y rounded-lg border">
        {rows.map((row) => (
          <li key={row.key} className="space-y-1.5 px-4 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-medium">{row.label}</span>

              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {row.used} / {row.limit}
              </span>
            </div>

            <Bar row={row} />

            <p className="text-xs text-muted-foreground">
              {row.remaining === 0 && row.resets_in > 0
                ? `Used up - more available in ${minutes(row.resets_in)}.`
                : row.description}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Bar({ row }: { row: LimitRead }) {
  const percent = Math.min(100, Math.round(pressure(row) * 100));

  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuenow={row.used}
      aria-valuemin={0}
      aria-valuemax={row.limit}
      aria-label={row.label}
    >
      <div
        className={`h-full rounded-full transition-all ${barColour(percent)}`}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

/**
 * Colour is a hint, never the message.
 *
 * The numbers above the bar say the same thing in text, because
 * roughly one man in twelve cannot separate red from green - and
 * because a bar with no scale tells you a proportion of something you
 * cannot see.
 */
function barColour(percent: number): string {
  if (percent >= 100) return "bg-red-500";
  if (percent >= 80) return "bg-amber-500";

  return "bg-emerald-500";
}

function pressure(row: LimitRead): number {
  return row.limit > 0 ? row.used / row.limit : 0;
}

function byPressure(a: LimitRead, b: LimitRead): number {
  return pressure(b) - pressure(a);
}

function minutes(seconds: number): string {
  if (seconds <= 60) return "under a minute";

  const value = Math.round(seconds / 60);

  return `${value} minute${value === 1 ? "" : "s"}`;
}
