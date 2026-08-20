"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

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

import { ConnectDialog } from "@/components/plugins/connect-dialog";
import { disconnectPlugin, listConnections, listPlugins } from "@/lib/api/plugins";

export default function PluginsPage() {
  const [connecting, setConnecting] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const plugins = useQuery({ queryKey: ["plugins"], queryFn: listPlugins });
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: listConnections,
  });

  const disconnect = useMutation({
    mutationFn: disconnectPlugin,
    onSuccess: () => {
      toast.success("Disconnected");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: () => toast.error("Could not disconnect. Try again."),
  });

  // A lookup, not a .find() inside the render loop. With four plugins
  // the difference is nothing; the habit is what matters, because the
  // nested version is quietly O(plugins x connections).
  const byKey = new Map(
    (connections.data ?? []).map((c) => [c.plugin_key, c]),
  );

  if (plugins.isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-44 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Services</h1>
        <p className="text-muted-foreground">
          Connect an account so your agents can use it.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {(plugins.data ?? []).map((plugin) => {
          const connection = byKey.get(plugin.key);
          const isConnected = connection?.status === "connected";

          return (
            <Card key={plugin.key} className="flex flex-col">
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="text-base">{plugin.label}</CardTitle>
                    <CardDescription className="line-clamp-2">
                      {plugin.description}
                    </CardDescription>
                  </div>

                  <span aria-hidden className="text-2xl leading-none">
                    {plugin.icon}
                  </span>
                </div>
              </CardHeader>

              <CardContent className="mt-auto space-y-3">
                <div className="flex items-center gap-2">
                  {/* The dot is decorative; the WORD carries the state,
                      so it is readable without seeing colour. */}
                  <span
                    aria-hidden
                    className={
                      isConnected
                        ? "size-2 rounded-full bg-emerald-500"
                        : "size-2 rounded-full bg-muted-foreground/40"
                    }
                  />
                  <span className="text-sm">
                    {isConnected ? "Connected" : "Not connected"}
                  </span>

                  {connection?.account_label && (
                    <span className="truncate text-xs text-muted-foreground">
                      {connection.account_label}
                    </span>
                  )}
                </div>

                <div className="flex items-center justify-between">
                  <Badge variant="secondary">
                    {plugin.tool_count ?? 0} tools
                  </Badge>

                  <div className="flex gap-2">
                    <Button asChild size="sm" variant="ghost">
                      <Link href={`/plugins/${plugin.key}`}>View tools</Link>
                    </Button>

                    {isConnected ? (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => disconnect.mutate(plugin.key)}
                        disabled={disconnect.isPending}
                      >
                        Disconnect
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        onClick={() => setConnecting(plugin.key)}
                      >
                        Connect
                      </Button>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* One dialog for the whole page, driven by which key is set.
          Rendering a dialog per card would mount N copies of the same
          form and N pieces of duplicate state. */}
      {connecting && (
        <ConnectDialog
          pluginKey={connecting}
          open={Boolean(connecting)}
          onOpenChange={(open) => !open && setConnecting(null)}
        />
      )}
    </div>
  );
}
