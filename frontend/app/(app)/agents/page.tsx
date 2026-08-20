"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { listAgents } from "@/lib/api/agents";
import { prettyNamespace } from "@/lib/tools";

export default function AgentsPage() {
  const agents = useQuery({ queryKey: ["agents"], queryFn: listAgents });

  if (agents.isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-2">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-40 w-full" />
        ))}
      </div>
    );
  }

  const items = agents.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Agents</h1>
          <p className="text-muted-foreground">
            Each agent has its own instructions and its own set of tools.
          </p>
        </div>

        <Button asChild>
          <Link href="/agents/new">+ Create Agent</Link>
        </Button>
      </div>

      {items.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center">
          <p className="font-medium">No agents yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Create one and choose what it is allowed to do.
          </p>
          <Button asChild className="mt-4">
            <Link href="/agents/new">Create your first agent</Link>
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {items.map((agent) => (
            <Card key={agent.id}>
              <CardHeader>
                <CardTitle className="text-base">{agent.name}</CardTitle>
                <CardDescription className="line-clamp-2">
                  {agent.description ?? "No description"}
                </CardDescription>
              </CardHeader>

              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  {(agent.namespaces ?? []).map((ns) => (
                    <Badge key={ns} variant="secondary">
                      {prettyNamespace(ns)}
                    </Badge>
                  ))}
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {agent.tool_count} tools enabled
                  </span>

                  <div className="flex gap-2">
                    <Button asChild size="sm" variant="ghost">
                      <Link href={`/agents/${agent.id}/settings`}>
                        Settings
                      </Link>
                    </Button>

                    <Button asChild size="sm">
                      <Link href={`/agents/${agent.id}/chat`}>Chat</Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
