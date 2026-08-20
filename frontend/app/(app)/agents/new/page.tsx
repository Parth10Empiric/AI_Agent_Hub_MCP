"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";

import { ToolPicker, type PickableTool } from "@/components/agents/tool-picker";
import { createAgent, type AgentCreatePayload } from "@/lib/api/agents";
import { getPlugin, listPlugins } from "@/lib/api/plugins";

const STEPS = ["Identity", "Instructions", "Services", "Tools"] as const;

const DRAFT_KEY = "agent-hub:new-agent-draft";

interface Draft {
  name: string;
  description: string;
  system_prompt: string;
  plugins: string[];
  tools: string[];
  // Whether the user has touched the tool list yet. Before they do, the
  // selection follows the plugin choice; afterwards their edits win.
  toolsTouched: boolean;
}

const EMPTY: Draft = {
  name: "",
  description: "",
  system_prompt:
    "You are a helpful assistant. Use the tools available to you to answer questions accurately. If a tool fails, explain what happened instead of guessing.",
  plugins: [],
  tools: [],
  toolsTouched: false,
};

export default function NewAgentPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [restored, setRestored] = useState(false);

  /**
   * Restore the draft on mount.
   *
   * In an effect rather than in useState's initialiser because
   * sessionStorage does not exist on the server. Reading it during the
   * first render would crash the server render, and returning different
   * values on server and client is a hydration mismatch.
   */
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(DRAFT_KEY);
      if (saved) setDraft({ ...EMPTY, ...JSON.parse(saved) });
    } catch {
      // Corrupt or unavailable storage is not worth an error. Starting
      // from a blank form is a perfectly good outcome.
    }

    setRestored(true);
  }, []);

  // Save on every change. sessionStorage, not localStorage: a
  // half-finished agent is scoped to this tab and should not reappear
  // in a different one weeks later.
  useEffect(() => {
    if (!restored) return;

    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    } catch {
      // Private mode or a full quota. Losing autosave is acceptable;
      // breaking the form is not.
    }
  }, [draft, restored]);

  const plugins = useQuery({ queryKey: ["plugins"], queryFn: listPlugins });

  /**
   * Fetch the tool list for each chosen service, in parallel.
   *
   * useQueries rather than a loop of useQuery, because the number of
   * queries changes as the user ticks services - and hooks may not be
   * called conditionally or in a loop of varying length.
   */
  const pluginDetails = useQueries({
    queries: draft.plugins.map((key) => ({
      queryKey: ["plugin", key],
      queryFn: () => getPlugin(key),
    })),
  });

  /**
   * Every tool offered by the chosen services, flattened.
   *
   * Computed plainly on each render rather than memoised. useQueries
   * returns a NEW array every render, so a useMemo keyed on it would
   * recompute every time anyway - all the memo would add is a
   * dependency list that lies. Flattening ~60 objects is far cheaper
   * than the bug that lying list would eventually cause.
   */
  const availableTools: PickableTool[] = pluginDetails.flatMap((query) =>
    (query.data?.tools ?? []).map((tool) => ({
      name: tool.name,
      namespace: query.data?.key ?? null,
      description: tool.description,
      operation: tool.operation,
      risk_level: tool.risk_level,
      read_only: tool.read_only,
      requires_approval: tool.requires_approval,
    })),
  );

  /**
   * Default the selection to every READ tool.
   *
   * This is the Phase 3.6 rule mirrored in the UI: reads on, writes
   * off, so a new agent is safe by default and the user opts in to
   * anything that changes their accounts.
   *
   * Only runs until the user edits the list themselves.
   */
  // A stable dependency. `availableTools` is a new array every render,
  // so depending on it directly would run this effect every render.
  const readToolNames = availableTools
    .filter((tool) => tool.read_only)
    .map((tool) => tool.name)
    .join(",");

  useEffect(() => {
    if (draft.toolsTouched || !readToolNames) return;

    const reads = readToolNames.split(",");

    setDraft((d) =>
      // Compare before setting, or this effect re-triggers itself
      // forever: setDraft -> re-render -> effect -> setDraft.
      d.tools.join(",") === readToolNames ? d : { ...d, tools: reads },
    );
  }, [readToolNames, draft.toolsTouched]);

  const create = useMutation({
    mutationFn: () => {
      const payload: AgentCreatePayload = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        system_prompt: draft.system_prompt.trim(),
        plugins: draft.plugins,
        tools: Object.fromEntries(
          draft.tools.map((name) => [name, { enabled: true }]),
        ),
      };

      return createAgent(payload);
    },

    onSuccess: (agent) => {
      sessionStorage.removeItem(DRAFT_KEY);
      toast.success(`${agent.name} created`);
      router.replace(`/agents/${agent.id}/chat`);
    },
  });

  const canContinue = [
    draft.name.trim().length > 0,
    draft.system_prompt.trim().length > 0,
    draft.plugins.length > 0,
    draft.tools.length > 0,
  ][step];

  if (!restored) return <Skeleton className="h-96 w-full" />;

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Create an agent</h1>
        <p className="text-muted-foreground">
          Your progress is saved as you go.
        </p>
      </div>

      {/* An ordered list, not a row of divs: the steps genuinely are a
          sequence, and a screen reader should hear them as one. */}
      <ol className="flex flex-wrap gap-2">
        {STEPS.map((label, index) => (
          <li key={label}>
            <button
              type="button"
              // Only steps already reached are clickable; jumping ahead
              // would skip validation of the steps in between.
              disabled={index > step}
              onClick={() => setStep(index)}
              aria-current={index === step ? "step" : undefined}
              className={`rounded-md border px-3 py-1.5 text-sm transition-colors disabled:opacity-40 ${
                index === step
                  ? "border-foreground bg-foreground text-background"
                  : "hover:bg-muted"
              }`}
            >
              {index + 1}. {label}
            </button>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="Developer Agent"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description (optional)</Label>
            <Input
              id="description"
              value={draft.description}
              onChange={(e) =>
                setDraft({ ...draft, description: e.target.value })
              }
              placeholder="Helps with repositories and documentation"
            />
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-2">
          <Label htmlFor="prompt">Instructions</Label>
          <Textarea
            id="prompt"
            rows={10}
            value={draft.system_prompt}
            onChange={(e) =>
              setDraft({ ...draft, system_prompt: e.target.value })
            }
          />
          <p className="text-sm text-muted-foreground">
            This is sent to the model before every conversation. Tell it
            what it is for and how to behave when something goes wrong.
          </p>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Choose which of your connected services this agent can use.
          </p>

          {(plugins.data ?? []).map((plugin) => {
            const checked = draft.plugins.includes(plugin.key);

            return (
              <label
                key={plugin.key}
                className={`flex items-center gap-3 rounded-lg border p-3 ${
                  plugin.connected ? "cursor-pointer" : "opacity-50"
                }`}
              >
                <Checkbox
                  checked={checked}
                  // A service that is not connected has no credentials,
                  // so an agent using it could only ever fail.
                  disabled={!plugin.connected}
                  onCheckedChange={(value) =>
                    setDraft({
                      ...draft,
                      plugins:
                        value === true
                          ? [...draft.plugins, plugin.key]
                          : draft.plugins.filter((k) => k !== plugin.key),
                      // Changing services changes which tools exist, so
                      // let the read-only default apply again.
                      toolsTouched: false,
                    })
                  }
                />

                <span className="flex-1">
                  <span className="font-medium">{plugin.label}</span>
                  <span className="block text-sm text-muted-foreground">
                    {plugin.connected
                      ? `${plugin.tool_count} tools available`
                      : "Not connected yet"}
                  </span>
                </span>
              </label>
            );
          })}

          {(plugins.data ?? []).every((p) => !p.connected) && (
            <Alert>
              <AlertDescription>
                You have not connected any services yet. Connect one
                first, then come back.
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}

      {step === 3 && (
        <>
          {pluginDetails.some((q) => q.isLoading) ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <ToolPicker
              tools={availableTools}
              selected={new Set(draft.tools)}
              onChange={(next) =>
                setDraft({
                  ...draft,
                  tools: [...next],
                  toolsTouched: true,
                })
              }
            />
          )}
        </>
      )}

      {create.error && (
        <Alert variant="destructive">
          <AlertDescription>
            {create.error instanceof Error
              ? create.error.message
              : "Could not create the agent."}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center justify-between border-t pt-4">
        <Button
          type="button"
          variant="ghost"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
        >
          Back
        </Button>

        {step < STEPS.length - 1 ? (
          <Button
            type="button"
            onClick={() => setStep((s) => s + 1)}
            disabled={!canContinue}
          >
            Continue
          </Button>
        ) : (
          <Button
            type="button"
            onClick={() => create.mutate()}
            disabled={!canContinue || create.isPending}
          >
            {create.isPending ? "Creating..." : "Create agent"}
          </Button>
        )}
      </div>
    </div>
  );
}
