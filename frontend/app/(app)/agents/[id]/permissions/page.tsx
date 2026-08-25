"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";

import {
  getAgentAudit,
  getAgentScopes,
  grantScope,
  revokeScope,
} from "@/lib/api/permissions";
import { ApiError } from "@/lib/api/client";
import { formatRelative, prettyNamespace } from "@/lib/tools";
import type { ScopeOption } from "@/lib/types";

/**
 * What this agent is ALLOWED to do, as opposed to which tools are
 * switched on.
 *
 * THE TWO ARE DIFFERENT QUESTIONS, AND BOTH ARE CHECKED
 *
 *     Tools (settings)   which tools are switched on?  a preference
 *     Permissions (here) what class of action at all?  the envelope
 *
 * The backend checks them independently before any tool runs, so
 * ticking a write tool in settings does NOT by itself let this agent
 * write. Two deliberate actions stand between "create agent" and "may
 * modify a client's repository", and this screen is the second one.
 *
 * WHY SCOPES AND NOT JUST MORE CHECKBOXES
 *
 * Sixty-one checkboxes is not a permission model - it is a form nobody
 * reads, and people tick "everything" to make it go away. "This agent
 * may read GitHub" is one sentence a person can actually mean, and it
 * keeps meaning the same thing when a new GitHub read tool ships next
 * month.
 */
export default function AgentPermissionsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const [showAudit, setShowAudit] = useState(false);

  const scopes = useQuery({
    queryKey: ["agent-scopes", id],
    queryFn: () => getAgentScopes(id),
  });

  const audit = useQuery({
    queryKey: ["agent-audit", id],
    queryFn: () => getAgentAudit(id, { limit: 25 }),
    enabled: showAudit,
  });

  const toggle = useMutation({
    mutationFn: ({ scope, grant }: { scope: string; grant: boolean }) =>
      grant ? grantScope(id, scope) : revokeScope(id, scope),

    onSuccess: (data, variables) => {
      // The response IS the new state, so it is written straight into
      // the cache rather than triggering a refetch. One round trip, and
      // no flicker while a second request confirms what we already
      // know.
      queryClient.setQueryData(["agent-scopes", id], data);
      queryClient.invalidateQueries({ queryKey: ["agent-audit", id] });

      toast.success(
        variables.grant
          ? `Granted ${variables.scope}`
          : `Revoked ${variables.scope}`,
      );
    },

    onError: (error) => {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not change permissions.",
      );
    },
  });

  const groups = useMemo(
    () => groupByService(scopes.data?.available ?? []),
    [scopes.data],
  );

  /**
   * Which services get a full section, and which get one line.
   *
   * A service is shown in full when the user has connected it, OR when
   * this agent already holds a grant over it. The second half is not
   * an edge case: connect GitHub, grant a write, disconnect GitHub -
   * the grant is still recorded and still applies the moment the
   * service is reconnected. Tucking it away with the rest would make a
   * live permission unrevocable from the only screen that revokes
   * permissions.
   */
  const connectedGroups = useMemo(
    () =>
      groups.filter(
        (group) =>
          group.connected || group.options.some((o) => o.granted),
      ),
    [groups],
  );

  const unconnected = useMemo(
    () => groups.filter((group) => !connectedGroups.includes(group)),
    [groups, connectedGroups],
  );

  if (scopes.isLoading) return <Skeleton className="h-96 w-full" />;

  if (scopes.isError || !scopes.data) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          Could not load this agent&apos;s permissions.{" "}
          <Link href="/agents" className="underline">
            Back to agents
          </Link>
        </AlertDescription>
      </Alert>
    );
  }

  const grantedCount = scopes.data.granted?.length ?? 0;

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <div>
        <Link
          href={`/agents/${id}/settings`}
          className="text-sm text-muted-foreground hover:underline"
        >
          ← Settings
        </Link>

        <h1 className="mt-2 text-2xl font-semibold">Permissions</h1>

        <p className="mt-2 text-sm text-muted-foreground">
          What this agent is allowed to do, whatever it is asked. A tool
          only runs if it is switched on in settings <em>and</em> covered
          by a permission here.
        </p>
      </div>

      {grantedCount === 0 && (
        <Alert>
          <AlertDescription>
            This agent has no permissions and cannot do anything yet.
            Grant at least one &ldquo;read&rdquo; permission below.
          </AlertDescription>
        </Alert>
      )}

      {/* SERVICES WITH NO ACCOUNT BEHIND THEM ARE NOT CHOICES.
          A scope over a service the user has not connected cannot do
          anything: the tool would be offered, called, and fail at the
          credential resolver. Listing them beside the real ones turned
          this page into a wall of switches, and a wall is what people
          click through without reading - on the one screen where
          reading is the entire point. */}
      {connectedGroups.map((group) => (
        <section key={group.service} className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-semibold">
              {prettyNamespace(group.service)}
            </h2>

            {/* An agent can hold a grant for a service that was
                disconnected afterwards. The grant is still real and
                still has to be revocable, so the section stays -
                labelled, rather than hidden. A permission you cannot
                see is a permission you cannot take away. */}
            {!group.connected && (
              <span className="rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
                not connected
              </span>
            )}
          </div>

          <ul className="divide-y rounded-lg border">
            {group.options.map((option) => (
              <li
                key={option.scope}
                className="flex items-center gap-3 px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {describe(option)}

                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                        ACTION_CLASS[option.action] ?? ACTION_CLASS.read
                      }`}
                    >
                      {option.action}
                    </span>
                  </p>

                  <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {option.scope}
                  </p>
                </div>

                {/* The number is what makes the choice legible.
                    "Covers 7 tools" is a sentence someone can weigh;
                    a bare scope string is not. */}
                <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                  {option.tool_count}{" "}
                  {option.tool_count === 1 ? "tool" : "tools"}
                </span>

                <Switch
                  checked={option.granted}
                  disabled={toggle.isPending}
                  aria-label={`${option.granted ? "Revoke" : "Grant"} ${option.scope}`}
                  onCheckedChange={(next) =>
                    toggle.mutate({ scope: option.scope, grant: next })
                  }
                />
              </li>
            ))}
          </ul>
        </section>
      ))}

      {/* One line per service the user has never connected, instead of
          a section of switches that could not take effect. It names
          them rather than dropping them silently: "GitHub is missing
          from this page" is a worse puzzle than one row saying why. */}
      {unconnected.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold text-muted-foreground">
            Not connected
          </h2>

          <ul className="divide-y rounded-lg border border-dashed">
            {unconnected.map((group) => (
              <li
                key={group.service}
                className="flex items-center gap-3 px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {prettyNamespace(group.service)}
                  </p>

                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Connect an account to grant permissions for its{" "}
                    {group.options.length}{" "}
                    {group.options.length === 1
                      ? "permission"
                      : "permissions"}
                    .
                  </p>
                </div>

                <Button asChild size="sm" variant="outline">
                  <Link href="/plugins">Connect</Link>
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">History</h2>

          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setShowAudit((open) => !open)}
          >
            {showAudit ? "Hide" : "Show"}
          </Button>
        </div>

        <p className="text-sm text-muted-foreground">
          Every permission change, kept permanently. This record cannot
          be edited or deleted - not from this screen, and not by
          deleting the agent.
        </p>

        {showAudit &&
          (audit.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : (audit.data?.items.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing yet.</p>
          ) : (
            <ul className="divide-y rounded-lg border text-sm">
              {(audit.data?.items ?? []).map((entry) => (
                <li
                  key={entry.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2"
                >
                  <span className="font-mono text-xs">{entry.action}</span>

                  <span className="font-mono text-xs text-muted-foreground">
                    {entry.scope ?? entry.tool_name ?? ""}
                  </span>

                  <span className="ml-auto text-xs text-muted-foreground">
                    {formatRelative(entry.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          ))}
      </section>
    </div>
  );
}

/**
 * Group the catalogue by service, with wildcards first.
 *
 * "Everything on GitHub" belongs above "issues only" because it is the
 * choice most people want, and burying it under a list of specific
 * resources is how a user ends up granting eight narrow scopes by hand
 * that one broad one would have covered.
 */
function groupByService(options: ScopeOption[]): ScopeGroup[] {
  const map = new Map<string, ScopeOption[]>();

  for (const option of options) {
    const bucket = map.get(option.service);

    if (bucket) bucket.push(option);
    else map.set(option.service, [option]);
  }

  return [...map.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([service, items]) => ({
      service,
      options: items.sort(byBreadthThenName),

      // Every scope in a group shares a service, so they all carry the
      // same answer - but reading it off the group rather than off one
      // arbitrary member is what keeps this true if that ever changes.
      connected: items.every((option) => option.connected),
    }));
}

interface ScopeGroup {
  service: string;
  options: ScopeOption[];
  connected: boolean;
}

function byBreadthThenName(a: ScopeOption, b: ScopeOption): number {
  const wildcardA = a.resource === "*" ? 0 : 1;
  const wildcardB = b.resource === "*" ? 0 : 1;

  if (wildcardA !== wildcardB) return wildcardA - wildcardB;

  const rank = ACTION_ORDER.indexOf(a.action) - ACTION_ORDER.indexOf(b.action);

  if (rank !== 0) return rank;

  return a.scope.localeCompare(b.scope);
}

/**
 * A scope, in words.
 *
 * "github:*:write" is precise and means nothing to most people.
 * "Change anything on GitHub" is the sentence that makes someone
 * hesitate before switching it on - which is the entire job of this
 * screen.
 */
function describe(option: ScopeOption): string {
  const verb = ACTION_VERB[option.action] ?? option.action;

  const target =
    option.resource === "*"
      ? `anything on ${prettyNamespace(option.service)}`
      : `${option.resource.replace(/_/g, " ")} on ${prettyNamespace(
          option.service,
        )}`;

  return `${verb} ${target}`;
}

const ACTION_ORDER = ["read", "write", "admin"];

const ACTION_VERB: Record<string, string> = {
  read: "Read",
  write: "Create and change",
  admin: "Change who can see",
};

const ACTION_CLASS: Record<string, string> = {
  read: "text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-950 dark:border-emerald-900",
  write:
    "text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-300 dark:bg-amber-950 dark:border-amber-900",
  admin:
    "text-red-700 bg-red-50 border-red-300 dark:text-red-300 dark:bg-red-950 dark:border-red-900",
};
