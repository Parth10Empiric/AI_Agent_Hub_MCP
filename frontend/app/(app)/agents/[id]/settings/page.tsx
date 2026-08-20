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

import { ToolPicker, type PickableTool } from "@/components/agents/tool-picker";
import {
  archiveAgent,
  getAgentTools,
  setAgentTools,
  updateAgent,
} from "@/lib/api/agents";

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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmArchive, setConfirmArchive] = useState(false);

  const agent = useQuery({
    queryKey: ["agent-tools", id],
    queryFn: () => getAgentTools(id),
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
    setSelected(
      new Set(
        (agent.data.tools ?? [])
          .filter((t) => t.enabled)
          .map((t) => t.tool_name),
      ),
    );
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

  const saveTools = useMutation({
    mutationFn: () => {
      // EVERY tool is sent, enabled true or false - not just the ticked
      // ones. The endpoint is a PUT: the body is the complete new
      // state, so an omitted tool would be indistinguishable from a
      // tool this client had never heard of.
      const payload = Object.fromEntries(
        (agent.data?.tools ?? []).map((tool) => [
          tool.tool_name,
          { enabled: selected.has(tool.tool_name) },
        ]),
      );

      return setAgentTools(id, payload);
    },
    onSuccess: () => {
      toast.success("Tools updated");
      queryClient.invalidateQueries({ queryKey: ["agent-tools", id] });
      queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: () => toast.error("Could not update tools."),
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

  // Tools the agent knows about but whose service is no longer
  // connected. Shown as unavailable rather than hidden, so a user can
  // see WHY an agent stopped being able to do something.
  const tools: PickableTool[] = (agent.data.tools ?? []).map((tool) => ({
    name: tool.tool_name,
    namespace: tool.namespace,
    description: tool.description ?? null,
    operation: tool.operation ?? "read",
    risk_level: tool.risk_level ?? "safe",
    read_only: (tool.operation ?? "read") === "read",
    requires_approval: tool.requires_approval,
  }));

  const unavailable = (agent.data.tools ?? []).filter(
    (t) => !t.available,
  ).length;

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

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Tools</h2>

          <Button
            onClick={() => saveTools.mutate()}
            disabled={saveTools.isPending}
          >
            {saveTools.isPending ? "Saving..." : "Save tools"}
          </Button>
        </div>

        {unavailable > 0 && (
          <Alert>
            <AlertDescription>
              {unavailable} tool{unavailable === 1 ? "" : "s"} cannot be
              used right now because the service is not connected.
            </AlertDescription>
          </Alert>
        )}

        <ToolPicker
          tools={tools}
          selected={selected}
          onChange={setSelected}
        />
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
