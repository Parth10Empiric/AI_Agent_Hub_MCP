"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { UsageMeter } from "@/components/limits/usage-meter";
import { archiveAgent, getAgentTools, updateAgent } from "@/lib/api/agents";
import { getAgentScopes } from "@/lib/api/permissions";
import { prettyNamespace } from "@/lib/tools";
import type { ScopeOption } from "@/lib/types";

/**
 * A scope, in words.
 *
 * "github:issue:write" is precise and means nothing to most people.
 * "Create and change issues on GitHub" is the sentence someone can
 * actually weigh - and weighing it is the entire point.
 *
 * Deliberately the same phrasing as the permissions screen, so a
 * permission reads identically wherever it appears.
 */
function describeScope(option: ScopeOption): string {
  const verb =
    option.action === "write"
      ? "Create and change"
      : option.action === "admin"
        ? "Change who can see"
        : "Read";

  const target =
    option.resource === "*"
      ? `anything on ${prettyNamespace(option.service)}`
      : `${option.resource.replace(/_/g, " ")} on ${prettyNamespace(
          option.service,
        )}`;

  return `${verb} ${target}`;
}

export default function AgentSettingsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [confirmArchive, setConfirmArchive] = useState(false);

  const agent = useQuery({
    queryKey: ["agent-tools", id],
    queryFn: () => getAgentTools(id),
  });

  /**
   * The permissions, read-only, on the page the user is already on.
   *
   * Same query key as the permissions screen, so granting there and
   * coming back here shows the new state from cache - no refetch, no
   * flash of stale permissions.
   */
  const scopes = useQuery({
    queryKey: ["agent-scopes", id],
    queryFn: () => getAgentScopes(id),
  });

  /**
   * Copy server data into local form state, once it arrives.
   *
   * Guarded on `agent.data` identity rather than running on every
   * render, or every keystroke would be overwritten by the cached
   * server value the moment React re-rendered.
   */
  useEffect(() => {
    if (!agent.data) return;

    setName(agent.data.name);
    setDescription(agent.data.description ?? "");
    setPrompt(agent.data.system_prompt);
  }, [agent.data]);

  const saveDetails = useMutation({
    mutationFn: () =>
      updateAgent(id, {
        name: name.trim(),
        description: description.trim() || null,
        system_prompt: prompt.trim(),
      }),
    onSuccess: () => {
      toast.success("Saved");
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      queryClient.invalidateQueries({ queryKey: ["agent", id] });
    },
    onError: () => toast.error("Could not save. Try again."),
  });

  const archive = useMutation({
    mutationFn: () => archiveAgent(id),
    onSuccess: () => {
      toast.success("Agent archived");
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      router.replace("/agents");
    },
  });

  if (agent.isLoading) return <Skeleton className="h-96 w-full" />;

  if (agent.isError || !agent.data) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          Could not load this agent.{" "}
          <Link href="/agents" className="underline">
            Back to agents
          </Link>
        </AlertDescription>
      </Alert>
    );
  }

  const allTools = agent.data.tools ?? [];

  // What the current permissions actually buy, in one number.
  //
  // `permitted` is computed by the backend with the same policy the
  // executor runs - never re-derived here, because two implementations
  // of a permission rule drift and only one of them is the one that
  // decides.
  const allowedCount = allTools.filter((tool) => tool.permitted).length;

  // Tools whose service is no longer connected. Counted, not hidden, so
  // a user can see WHY an agent stopped being able to do something.
  const unavailable = allTools.filter((tool) => !tool.available).length;

  const granted = (scopes.data?.available ?? []).filter(
    (option) => option.granted,
  );

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <div>
        <Link
          href="/agents"
          className="text-sm text-muted-foreground hover:underline"
        >
          ← Agents
        </Link>
        <h1 className="mt-2 text-2xl font-semibold">Settings</h1>
      </div>

      <section className="space-y-4">
        <h2 className="font-semibold">Details</h2>

        <div className="space-y-2">
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Input
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="prompt">Instructions</Label>
          <Textarea
            id="prompt"
            rows={8}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>

        <Button
          onClick={() => saveDetails.mutate()}
          disabled={!name.trim() || !prompt.trim() || saveDetails.isPending}
        >
          {saveDetails.isPending ? "Saving..." : "Save details"}
        </Button>
      </section>

      {/* WHAT THIS AGENT CAN DO - ONE ANSWER, IN ONE PLACE.

          There used to be a grid of 108 checkboxes here as well, and it
          was a second gate on the same question: a ticked tool with no
          permission behind it silently could not run, so the page
          needed a warning, a badge and a dialog to explain itself.

          Nobody curates 108 boxes anyway - they hit "select all" and
          learn nothing. A permission is one sentence a person can mean
          ("this agent may read GitHub"), and it keeps meaning the same
          thing when a new GitHub tool ships next month. So permissions
          are now the only thing to switch, and this section shows what
          is switched on. */}
      <section className="space-y-3" data-testid="permissions-summary">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Permissions</h2>

          <Button asChild variant="outline" size="sm">
            <Link href={`/agents/${id}/permissions`}>
              Manage permissions
            </Link>
          </Button>
        </div>

        <p className="text-sm text-muted-foreground">
          What this agent is allowed to do, whatever it is asked. Every
          tool it has follows from these - there is nothing else to
          switch on.
        </p>

        {scopes.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : granted.length === 0 ? (
          <Alert data-testid="no-permissions-warning">
            <AlertDescription>
              This agent has no permissions, so it cannot do anything
              yet. Open <strong>Manage permissions</strong> and switch on
              at least one.
            </AlertDescription>
          </Alert>
        ) : (
          <>
            <ul className="divide-y rounded-lg border">
              {granted.map((option) => (
                <li
                  key={option.scope}
                  className="flex items-center gap-3 px-4 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">
                      {describeScope(option)}
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {option.scope}
                    </p>
                  </div>

                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                    {option.tool_count}{" "}
                    {option.tool_count === 1 ? "tool" : "tools"}
                  </span>
                </li>
              ))}
            </ul>

            {/* The bottom line, after de-duplicating tools that more
                than one permission covers. Two permissions of "7 tools"
                do not make 14. */}
            <p className="text-sm text-muted-foreground">
              <strong className="tabular-nums text-foreground">
                {allowedCount}
              </strong>{" "}
              of {allTools.length} tools are available to this agent.
            </p>
          </>
        )}

        {unavailable > 0 && (
          <Alert>
            <AlertDescription>
              {unavailable} tool{unavailable === 1 ? "" : "s"} cannot be
              used right now because the service is not connected.
            </AlertDescription>
          </Alert>
        )}
      </section>

      {/* The full meters live here rather than in the chat, where
          only the tightest one appears. This is the page someone opens
          when they want to know WHY. */}
      <section className="border-t pt-6">
        <UsageMeter agentId={id} />
      </section>

      <section className="space-y-3 border-t pt-6">
        <h2 className="font-semibold">Archive</h2>
        <p className="text-sm text-muted-foreground">
          Archiving hides the agent. Its conversations and history are
          kept.
        </p>

        <Button
          variant="destructive"
          onClick={() => setConfirmArchive(true)}
        >
          Archive agent
        </Button>
      </section>

      <Dialog open={confirmArchive} onOpenChange={setConfirmArchive}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Archive this agent?</DialogTitle>
            <DialogDescription>
              It will disappear from your list. Nothing is deleted.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmArchive(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => archive.mutate()}
              disabled={archive.isPending}
            >
              Archive
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
