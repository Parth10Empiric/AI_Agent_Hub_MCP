"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { UsageMeter } from "@/components/limits/usage-meter";

import { listActivity } from "@/lib/api/audit";
import { listApprovals } from "@/lib/api/approvals";
import { listConnections } from "@/lib/api/plugins";
import { formatRelative, prettyNamespace } from "@/lib/tools";
import type { AuditCategory, AuditEntryRead } from "@/lib/types";

/**
 * Everything Phase 5 does, where a person can actually see it.
 *
 * THE PROBLEM THIS SOLVES
 *
 * Security features are invisible when they work. A permission that
 * holds, a token that stays encrypted, a limit not yet reached and an
 * approval nobody had to answer all look exactly like nothing
 * happening.
 *
 * That is fine until somebody has to trust the system - at which point
 * "it is fine, I promise" is not an answer. This page is the answer:
 * what is connected, what has been spent, and every security-relevant
 * thing that has happened to the account.
 *
 * The question it is built to answer is a specific one:
 *
 *     "Did anything happen to my account that I did not do?"
 */
export default function SecurityPage() {
  const [category, setCategory] = useState<AuditCategory | undefined>();

  const activity = useQuery({
    queryKey: ["activity", category ?? "all"],
    queryFn: () => listActivity({ category, limit: 50 }),
  });

  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: listConnections,
  });

  const approvals = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => listApprovals({ status: "pending", limit: 20 }),
  });

  const pending = approvals.data?.items ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <div>
        <h1 className="text-2xl font-semibold">Security</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          What your agents can reach, what they have used, and
          everything that has happened to your account.
        </p>
      </div>

      {/* Anything waiting on the user comes FIRST. A pending approval
          is a turn that is stopped right now - it is the only thing on
          this page that is not history. */}
      {pending.length > 0 && (
        <Alert>
          <AlertDescription>
            <strong>
              {pending.length} action{pending.length === 1 ? "" : "s"}
            </strong>{" "}
            {pending.length === 1 ? "is" : "are"} waiting for your
            approval. Open the agent&apos;s chat to answer.
          </AlertDescription>
        </Alert>
      )}

      <UsageMeter />

      <section className="space-y-3">
        <h2 className="font-semibold">Connected accounts</h2>

        {connections.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : (connections.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing connected yet.{" "}
            <Link href="/plugins" className="underline">
              Connect a service
            </Link>
            .
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {(connections.data ?? []).map((connection) => (
              <li
                key={connection.plugin_key}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3"
              >
                <span className="text-sm font-medium">
                  {prettyNamespace(connection.plugin_key)}
                </span>

                {connection.account_label && (
                  <span className="text-sm text-muted-foreground">
                    {connection.account_label}
                  </span>
                )}

                <span
                  className={`ml-auto rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                    connection.status === "connected"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300"
                      : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
                  }`}
                >
                  {connection.status === "connected"
                    ? "Connected"
                    : "Needs reconnecting"}
                </span>
              </li>
            ))}
          </ul>
        )}

        <p className="text-xs text-muted-foreground">
          Access tokens are encrypted before they are stored and are
          never shown again - not here, not anywhere in this app.
        </p>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">Activity</h2>

          <div className="flex flex-wrap gap-1">
            {CATEGORIES.map((option) => (
              <Button
                key={option.value ?? "all"}
                type="button"
                size="sm"
                variant={category === option.value ? "default" : "ghost"}
                className="h-7 px-2 text-xs"
                onClick={() => setCategory(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </div>

        <p className="text-sm text-muted-foreground">
          This record cannot be edited or deleted - not from this
          screen, and not by the application at all.
        </p>

        {activity.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : (activity.data?.items ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing recorded yet.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border" data-testid="activity">
            {(activity.data?.items ?? []).map((entry) => (
              <ActivityRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ActivityRow({ entry }: { entry: AuditEntryRead }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="px-4 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span aria-hidden className={DOT[entry.category] ?? DOT.other}>
          ●
        </span>

        <span className="font-medium">{entry.label}</span>

        <span className="ml-auto text-xs text-muted-foreground">
          {formatRelative(entry.occurred_at)}
        </span>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-6 px-1.5 text-xs"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Hide" : "Details"}
        </Button>
      </div>

      {open && (
        <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground">
          <dt>action</dt>
          <dd>{entry.action}</dd>

          <dt>when</dt>
          <dd>{new Date(entry.occurred_at).toLocaleString()}</dd>

          {entry.actor_ip && (
            <>
              <dt>from</dt>
              <dd>{entry.actor_ip}</dd>
            </>
          )}

          {entry.request_id && (
            <>
              {/* The string a user quotes in a support message. It
                  finds the log line, the stack trace and every other
                  row from the same request. */}
              <dt>request</dt>
              <dd>{entry.request_id}</dd>
            </>
          )}

          {Object.entries(entry.metadata ?? {}).map(([key, value]) => (
            <div key={key} className="contents">
              <dt>{key}</dt>
              <dd className="break-all">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

const CATEGORIES: { value: AuditCategory | undefined; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "sign-in", label: "Sign-in" },
  { value: "permissions", label: "Permissions" },
  { value: "connections", label: "Connections" },
  { value: "approvals", label: "Approvals" },
];

/**
 * Colour groups the rows at a glance; the LABEL always says what
 * happened. Colour alone is not a signal - roughly one man in twelve
 * cannot separate red from green.
 */
const DOT: Record<string, string> = {
  "sign-in": "text-sky-500",
  permissions: "text-amber-500",
  connections: "text-emerald-500",
  approvals: "text-violet-500",
  other: "text-muted-foreground",
};
