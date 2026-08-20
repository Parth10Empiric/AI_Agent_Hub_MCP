"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

import { listAgents } from "@/lib/api/agents";
import { listConnections, listPlugins } from "@/lib/api/plugins";

export default function DashboardPage() {
  /**
   * Three independent queries, not one combined endpoint.
   *
   * They run in parallel, each caches under its own key, and each can
   * show its own loading and error state. Connecting a plugin
   * invalidates only the connections query - the agent list is not
   * refetched for nothing.
   */
  const agents = useQuery({ queryKey: ["agents"], queryFn: listAgents });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: listPlugins });
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: listConnections,
  });

  const connected = (connections.data ?? []).filter(
    (c) => c.status === "connected",
  );

  const isLoading = agents.isLoading || connections.isLoading;

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const hasAgents = (agents.data ?? []).length > 0;
  const hasConnections = connected.length > 0;

  /**
   * The empty state IS the onboarding (Phase 4.3).
   *
   * A new user has zero agents and zero plugins. Showing them an empty
   * table teaches nothing; showing them the three steps, with step 2
   * locked until step 1 is done, teaches the product's model - you
   * connect a service before an agent can use one.
   */
  if (!hasAgents) {
    return (
      <div className="mx-auto max-w-lg py-12 text-center">
        <h1 className="text-2xl font-semibold">No agents yet</h1>
        <p className="mt-2 text-muted-foreground">
          Three steps and your agent can answer questions about your own
          accounts.
        </p>

        <ol className="mt-8 space-y-3 text-left">
          <Step
            n={1}
            title="Connect a service"
            done={hasConnections}
            action={
              <Button asChild size="sm" variant={hasConnections ? "outline" : "default"}>
                <Link href="/plugins">
                  {hasConnections ? "Manage" : "Connect a service"}
                </Link>
              </Button>
            }
          />

          <Step
            n={2}
            title="Create an agent"
            done={false}
            action={
              <Button asChild size="sm" disabled={!hasConnections}>
                {/* Disabled until step 1 is done - an agent with no
                    connected service has nothing to act on. */}
                <Link href={hasConnections ? "/agents/new" : "#"}>
                  Create agent
                </Link>
              </Button>
            }
          />

          <Step n={3} title="Start chatting" done={false} />
        </ol>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Welcome back</h1>
        <Button asChild>
          <Link href="/agents/new">+ Create Agent</Link>
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Agents" value={agents.data?.length ?? 0} />
        <Stat label="Connected" value={connected.length} />
        <Stat label="Plugins available" value={plugins.data?.length ?? 0} />
        <Stat
          label="Tools enabled"
          value={(agents.data ?? []).reduce(
            (sum, a) => sum + (a.tool_count ?? 0),
            0,
          )}
        />
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Your agents
        </h2>

        <div className="grid gap-3 md:grid-cols-2">
          {(agents.data ?? []).map((agent) => (
            <Card key={agent.id}>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{agent.name}</CardTitle>
              </CardHeader>

              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  {(agent.namespaces ?? []).map((ns) => (
                    <Badge key={ns} variant="secondary">
                      {ns}
                    </Badge>
                  ))}
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {agent.tool_count} tools enabled
                  </span>

                  <Button asChild size="sm" variant="outline">
                    <Link href={`/agents/${agent.id}`}>Open</Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}

function Step({
  n,
  title,
  done,
  action,
}: {
  n: number;
  title: string;
  done: boolean;
  action?: React.ReactNode;
}) {
  return (
    <li className="flex items-center justify-between rounded-lg border p-4">
      <span className="flex items-center gap-3">
        <span
          className={
            done
              ? "flex size-6 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground"
              : "flex size-6 items-center justify-center rounded-full border text-xs"
          }
        >
          {done ? "✓" : n}
        </span>
        <span className={done ? "text-muted-foreground line-through" : ""}>
          {title}
        </span>
      </span>

      {action}
    </li>
  );
}
