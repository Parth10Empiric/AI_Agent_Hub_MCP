"use client";

import { useState } from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  getExecutionStats,
  listExecutions,
  type ExecutionFilters,
  type ExecutionListItem,
} from "@/lib/api/executions";
import { formatDuration, prettyToolName } from "@/lib/tools";

type StatusFilter = "all" | "success" | "failed" | "denied";

export default function ExecutionsPage() {
  const [status, setStatus] = useState<StatusFilter>("all");
  const [days, setDays] = useState(7);

  const filters: ExecutionFilters = {
    days,
    ...(status === "all" ? {} : { status }),
  };

  /**
   * One query owns the rows, the cursor, and every loaded page.
   *
   * WHY useInfiniteQuery AND NOT useQuery + useState
   *
   * The first version of this page kept the rows in a `pages` state
   * array and filled it from inside `queryFn`. That produced a real bug
   * a user hit within minutes:
   *
   *   1. pick "Today"      → fetches, fills `pages`
   *   2. pick "7 days"     → fetches, fills `pages`
   *   3. pick "Today" again
   *        - changing the filter cleared `pages`
   *        - the key ["executions", {days:1}] was ALREADY cached and
   *          still fresh, so TanStack served the cache and never ran
   *          `queryFn`
   *        - `setPages` lives inside `queryFn`, so it never ran
   *        → empty screen, and nothing in the Network tab
   *
   * Two lessons, both general:
   *
   *   Never do side effects inside `queryFn`. It runs only on a cache
   *   MISS, so anything hidden in it silently stops happening the
   *   moment caching works - which is when it is hardest to notice.
   *
   *   Never copy server data into component state. Two sources of truth
   *   drift, and the copy is the one the screen renders.
   *
   * useInfiniteQuery keeps every fetched page in the cache under one
   * key, so the rows are DERIVED from the cache instead of mirrored
   * into state. Returning to a previous filter renders instantly from
   * cache - which is the correct behaviour, not a missing request.
   */
  const executions = useInfiniteQuery({
    // The filters are IN the key. The cursor is NOT - it is page state
    // that belongs to this query, and putting it in the key would make
    // every page a separate cache entry that forgets the ones before.
    queryKey: ["executions", filters],

    queryFn: ({ pageParam }) =>
      listExecutions({ ...filters, cursor: pageParam, limit: 50 }),

    initialPageParam: undefined as string | undefined,

    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,

    // While a NEW filter loads, keep showing the previous rows instead
    // of blanking the screen. The flicker is what made the original bug
    // look like data loss rather than a slow request.
    placeholderData: keepPreviousData,
  });

  const stats = useQuery({
    queryKey: ["execution-stats", days],
    queryFn: () => getExecutionStats(days),
    placeholderData: keepPreviousData,
  });

  function changeFilter(next: Partial<{ status: StatusFilter; days: number }>) {
    // Only the filter changes. There is no cursor or row array to reset
    // any more - a different filter is a different query key, and its
    // pages live under that key.
    if (next.status !== undefined) setStatus(next.status);
    if (next.days !== undefined) setDays(next.days);
  }

  // Derived from the cache on every render. Never stored.
  const rows: ExecutionListItem[] =
    executions.data?.pages.flatMap((page) => page.items) ?? [];

  const grouped = groupByDay(rows);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Activity</h1>
          <p className="text-muted-foreground">
            Every tool your agents have used.
          </p>
        </div>

        <div className="flex gap-2">
          <Select
            value={status}
            onValueChange={(v) => changeFilter({ status: v as StatusFilter })}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="success">Worked</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
              <SelectItem value="denied">Not allowed</SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={String(days)}
            onValueChange={(v) => changeFilter({ days: Number(v) })}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1">Today</SelectItem>
              <SelectItem value="7">7 days</SelectItem>
              <SelectItem value="30">30 days</SelectItem>
              <SelectItem value="90">90 days</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Total runs" value={stats.data?.total ?? 0} />
        <Stat
          label="Worked"
          value={stats.data?.success ?? 0}
          tone="text-emerald-600 dark:text-emerald-400"
        />
        <Stat
          label="Failed"
          value={stats.data?.failed ?? 0}
          tone="text-red-600 dark:text-red-400"
        />
        <Stat
          label="Not allowed"
          value={stats.data?.denied ?? 0}
          tone="text-amber-600 dark:text-amber-400"
        />
      </div>

      {executions.isLoading && rows.length === 0 ? (
        <Skeleton className="h-64 w-full" />
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center text-muted-foreground">
          Nothing here yet. Chat with an agent and its tool activity will
          appear.
        </div>
      ) : (
        // aria-busy tells assistive tech the list is being refreshed
        // while the previous rows are still on screen.
        <div className="space-y-6" aria-busy={executions.isFetching}>
          {grouped.map(([day, items]) => (
            <section key={day} className="space-y-1">
              <h2 className="text-sm font-medium text-muted-foreground">
                {day}
              </h2>

              {/* The table scrolls inside its own container so a long
                  tool name never makes the whole PAGE scroll sideways. */}
              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full min-w-[40rem] text-sm">
                  <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">Status</th>
                      <th className="px-3 py-2 font-medium">Tool</th>
                      <th className="px-3 py-2 font-medium">Agent</th>
                      <th className="px-3 py-2 text-right font-medium">
                        Time
                      </th>
                    </tr>
                  </thead>

                  <tbody className="divide-y">
                    {items.map((item) => (
                      <tr key={item.id} className="hover:bg-muted/30">
                        <td className="px-3 py-2">
                          <StatusCell status={item.status} />
                        </td>

                        <td className="px-3 py-2">
                          <span className="font-medium">
                            {prettyToolName(item.tool_name, item.namespace)}
                          </span>
                          <span className="ml-2 text-xs text-muted-foreground">
                            {item.namespace}
                          </span>
                        </td>

                        <td className="px-3 py-2 text-muted-foreground">
                          {item.agent_name ?? "—"}
                        </td>

                        <td className="px-3 py-2 text-right font-mono text-xs tabular-nums text-muted-foreground">
                          {formatDuration(item.duration_ms)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}

          {executions.hasNextPage && (
            <Button
              variant="outline"
              onClick={() => executions.fetchNextPage()}
              disabled={executions.isFetchingNextPage}
            >
              {executions.isFetchingNextPage ? "Loading..." : "Load more"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className={`text-2xl font-semibold tabular-nums ${tone ?? ""}`}>
        {value}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

function StatusCell({ status }: { status: string }) {
  // Symbol AND word. The symbol is fast to scan; the word is what makes
  // the row readable to someone who cannot separate the colours.
  const map: Record<string, { glyph: string; label: string; cls: string }> = {
    success: {
      glyph: "✓",
      label: "Worked",
      cls: "text-emerald-600 dark:text-emerald-400",
    },
    denied: {
      glyph: "⊘",
      label: "Not allowed",
      cls: "text-amber-600 dark:text-amber-400",
    },
    failed: {
      glyph: "✕",
      label: "Failed",
      cls: "text-red-600 dark:text-red-400",
    },
  };

  const tone = map[status] ?? map.failed;

  return (
    <span className={`flex items-center gap-1.5 text-xs ${tone.cls}`}>
      <span aria-hidden className="font-mono">
        {tone.glyph}
      </span>
      {tone.label}
    </span>
  );
}

/**
 * Group rows under "Today" / "Yesterday" / a date.
 *
 * A Map preserves insertion order, and the rows already arrive newest
 * first, so the groups come out in the right order without a sort.
 */
function groupByDay(
  items: ExecutionListItem[],
): [string, ExecutionListItem[]][] {
  const groups = new Map<string, ExecutionListItem[]>();

  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86_400_000).toDateString();

  for (const item of items) {
    const date = new Date(item.started_at).toDateString();

    const label =
      date === today
        ? "Today"
        : date === yesterday
          ? "Yesterday"
          : new Date(item.started_at).toLocaleDateString();

    const bucket = groups.get(label);

    if (bucket) bucket.push(item);
    else groups.set(label, [item]);
  }

  return [...groups.entries()];
}
