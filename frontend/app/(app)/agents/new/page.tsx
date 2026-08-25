"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";

import { createAgent, type AgentCreatePayload } from "@/lib/api/agents";
import { listPlugins } from "@/lib/api/plugins";

/**
 * THREE STEPS, NOT FOUR.
 *
 * There used to be a "Tools" step: a grid of ~108 checkboxes, defaulted
 * to every read tool, that the user scrolled past on their way to
 * Create. It asked the same question as the permissions screen and gave
 * a worse answer - ticking a write tool there did nothing, because a
 * new agent is never seeded with write scopes, and the box went green
 * anyway.
 *
 * So the wizard now stops at the question it can answer honestly:
 * WHICH SERVICES. Which tools follow from the permissions, and those
 * are granted deliberately, afterwards, on a screen built for it.
 */
const STEPS = ["Identity", "Instructions", "Services"] as const;

const DRAFT_KEY = "agent-hub:new-agent-draft";

interface Draft {
  name: string;
  description: string;
  system_prompt: string;
  plugins: string[];
}

const EMPTY: Draft = {
  name: "",
  description: "",
  system_prompt:
    "You are a helpful assistant. Use the tools available to you to answer questions accurately. If a tool fails, explain what happened instead of guessing.",
  plugins: [],
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

      if (saved) {
        // Spread over EMPTY, so a draft saved by the FOUR-step version
        // of this page still opens: its extra `tools` key is ignored
        // and any field it lacks falls back to the default.
        const { name, description, system_prompt, plugins } = {
          ...EMPTY,
          ...JSON.parse(saved),
        };

        setDraft({ name, description, system_prompt, plugins });
      }
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

  const create = useMutation({
    mutationFn: () => {
      const payload: AgentCreatePayload = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        system_prompt: draft.system_prompt.trim(),
        plugins: draft.plugins,

        // No `tools` key at all. The server writes a row for every tool
        // the chosen services offer, and what the agent may actually
        // DO is decided by its scopes - which start as reads only.
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

          {/* A NEW AGENT IS GRANTED READS AND NOTHING ELSE.

              "I clicked Create" is not consent to modify somebody's
              GitHub account, so no write permission is ever seeded
              (api/scopes.py). Saying so HERE, before the click, beats
              letting someone discover it when the agent tells them it
              cannot write. */}
          {draft.plugins.length > 0 && (
            <Alert>
              <AlertDescription>
                Your agent starts able to <strong>read</strong> these
                services and change nothing. To let it create or edit
                things, open <strong>Permissions</strong> after creating
                it and switch on what you want.
              </AlertDescription>
            </Alert>
          )}
        </div>
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
