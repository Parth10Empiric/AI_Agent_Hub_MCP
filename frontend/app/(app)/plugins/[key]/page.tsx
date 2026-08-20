"use client";

import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";

import { getPlugin } from "@/lib/api/plugins";
import {
  OPERATION_HELP,
  OPERATION_LABEL,
  RISK_CLASS,
  RISK_LABEL,
  groupByOperation,
  highestRisk,
  prettyToolName,
} from "@/lib/tools";

/**
 * One service and everything it can do.
 *
 * Note `use(params)`: in Next.js 15 route params are a Promise, because
 * a route can start rendering before they are resolved. `use()` unwraps
 * it inside a client component.
 */
export default function PluginDetailPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key } = use(params);

  const plugin = useQuery({
    queryKey: ["plugin", key],
    queryFn: () => getPlugin(key),
  });

  if (plugin.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (plugin.isError || !plugin.data) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          Could not load this service.{" "}
          <Link href="/plugins" className="underline">
            Back to services
          </Link>
        </AlertDescription>
      </Alert>
    );
  }

  const detail = plugin.data;

  // The grouping is derived entirely from data the API sent. Nothing
  // about GitHub or Drive is written into this component.
  const groups = groupByOperation(detail.tools);

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link
            href="/plugins"
            className="text-sm text-muted-foreground hover:underline"
          >
            ← Services
          </Link>

          <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold">
            <span aria-hidden>{detail.icon}</span>
            {detail.label}
          </h1>

          <p className="text-muted-foreground">{detail.description}</p>
        </div>

        <Badge variant="secondary" className="shrink-0">
          {detail.tool_count} tools
        </Badge>
      </div>

      {groups.map(({ operation, items }) => (
        <section key={operation} className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 border-b pb-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide">
              {OPERATION_LABEL[operation]}
            </h2>

            <span className="text-xs text-muted-foreground">
              {items.length}
            </span>

            {/* The group is badged with its WORST tool, not an average.
                A group containing one critical tool is a critical
                group - averaging would hide exactly the thing the user
                needs to notice. */}
            <span
              className={`rounded border px-2 py-0.5 text-xs font-medium ${
                RISK_CLASS[highestRisk(items)]
              }`}
            >
              {RISK_LABEL[highestRisk(items)]}
            </span>

            <span className="text-xs text-muted-foreground">
              {OPERATION_HELP[operation]}
            </span>
          </div>

          <ul className="divide-y">
            {items.map((tool) => (
              <li
                key={tool.name}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2"
              >
                <span className="font-medium">
                  {prettyToolName(tool.name, detail.key)}
                </span>

                <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
                  {tool.description ?? tool.title ?? ""}
                </span>

                {tool.requires_approval && (
                  <span className="text-xs text-amber-700 dark:text-amber-400">
                    Asks before running
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}

      {detail.connected ? (
        <p className="text-sm text-muted-foreground">
          Connected{detail.account_label ? ` as ${detail.account_label}` : ""}.
        </p>
      ) : (
        <Button asChild>
          <Link href="/plugins">Connect this service</Link>
        </Button>
      )}
    </div>
  );
}
