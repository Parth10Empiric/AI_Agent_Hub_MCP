"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";

import { setAgentServices } from "@/lib/api/agents";
import { listPlugins } from "@/lib/api/plugins";
import { ApiError } from "@/lib/api/client";
import { prettyNamespace } from "@/lib/tools";

/**
 * Which services this agent draws tools from.
 *
 * THE SCREEN THAT WAS MISSING, AND THE BUG IT CLOSES
 *
 * An agent can only use tools from a service it was GIVEN. That set was
 * chosen once, in the create wizard, and nothing could change it
 * afterwards - the backend deliberately never widens an agent on its
 * own, so connecting Google Drive later gave every existing agent
 * exactly nothing.
 *
 * Meanwhile the permissions page let you grant "google_drive:*:read",
 * because that is a different gate. The result was a permission that
 * looked granted and did nothing: the agent kept answering that Drive
 * was "switched off in this agent's tool settings", pointing at a
 * screen with no such switch. This is that switch.
 *
 * TWO STEPS, ON PURPOSE, AND THEY ARE NOT THE SAME QUESTION
 *
 *     Connect (plugins)   do I have an account?      once, per user
 *     Add (here)          may THIS agent use it?     per agent
 *
 * Collapsing them would mean connecting Drive silently hands it to
 * every agent you have ever made, including the one you gave to a
 * client. The cost of keeping them apart is this section; the cost of
 * merging them is a capability nobody granted.
 */
export function ServicesSection({
  agentId,
  services,
}: {
  agentId: string;
  services: string[];
}) {
  const queryClient = useQueryClient();

  const plugins = useQuery({
    queryKey: ["plugins"],
    queryFn: listPlugins,
  });

  /**
   * The draft, as a Set.
   *
   * Seeded from the server value and re-seeded whenever it changes -
   * including after a save, so the "unsaved changes" state clears
   * itself rather than needing to be reset by hand.
   *
   * `services.join()` rather than `services` as the dependency: the
   * array is a fresh object on every render of the parent, and
   * depending on its identity would wipe the user's ticks mid-edit.
   */
  const [picked, setPicked] = useState<Set<string>>(new Set(services));

  useEffect(() => {
    setPicked(new Set(services));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [services.join(",")]);

  const added = useMemo(
    () => [...picked].filter((key) => !services.includes(key)).sort(),
    [picked, services],
  );

  const removed = useMemo(
    () => services.filter((key) => !picked.has(key)).sort(),
    [picked, services],
  );

  const dirty = added.length > 0 || removed.length > 0;

  const save = useMutation({
    mutationFn: () => setAgentServices(agentId, [...picked].sort()),

    onSuccess: (detail) => {
      queryClient.setQueryData(["agent-tools", agentId], detail);
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      queryClient.invalidateQueries({ queryKey: ["agent", agentId] });

      // NOT optional. Adding a service seeds read permissions and
      // removing one revokes them, so the permissions screen and its
      // history are both stale the moment this succeeds.
      queryClient.invalidateQueries({ queryKey: ["agent-scopes", agentId] });
      queryClient.invalidateQueries({ queryKey: ["agent-audit", agentId] });

      toast.success("Services updated");
    },

    onError: (error) =>
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not update services.",
      ),
  });

  const catalogue = plugins.data ?? [];

  return (
    <section className="space-y-3" data-testid="services-section">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">Services</h2>

        <Button asChild variant="outline" size="sm">
          <Link href="/plugins">Connect a service</Link>
        </Button>
      </div>

      <p className="text-sm text-muted-foreground">
        Which of your connected services this agent can draw tools from.
        Connecting a service does not give it to an agent - that is this
        list, and it is deliberately per agent.
      </p>

      {plugins.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : catalogue.length === 0 ? (
        <Alert>
          <AlertDescription>
            No services are available on this server.
          </AlertDescription>
        </Alert>
      ) : (
        <div className="space-y-2">
          {catalogue.map((plugin) => {
            const checked = picked.has(plugin.key);

            // An unconnected service has no credentials, so adding it
            // could only ever produce tools that fail at the credential
            // resolver. Removing one stays possible whatever its
            // connection state - see the disabled rule below.
            const blocked = !plugin.connected && !checked;

            return (
              <label
                key={plugin.key}
                className={`flex items-center gap-3 rounded-lg border p-3 ${
                  blocked ? "opacity-50" : "cursor-pointer"
                }`}
              >
                <Checkbox
                  checked={checked}
                  disabled={blocked || save.isPending}
                  aria-label={`${checked ? "Remove" : "Add"} ${plugin.label}`}
                  onCheckedChange={(value) =>
                    setPicked((current) => {
                      const next = new Set(current);

                      if (value === true) next.add(plugin.key);
                      else next.delete(plugin.key);

                      return next;
                    })
                  }
                />

                <span className="min-w-0 flex-1">
                  <span className="font-medium">{plugin.label}</span>

                  <span className="block text-sm text-muted-foreground">
                    {plugin.connected
                      ? `${plugin.tool_count} tools`
                      : "Not connected - connect it first"}
                  </span>
                </span>

                {services.includes(plugin.key) && (
                  <span className="shrink-0 rounded border px-2 py-0.5 text-xs text-muted-foreground">
                    on this agent
                  </span>
                )}
              </label>
            );
          })}
        </div>
      )}

      {/* WHAT SAVING WILL DO, BEFORE THE CLICK.

          Both directions change permissions, and a permission change
          the user did not expect is the one thing this screen must
          never produce. Adding seeds reads; removing revokes. Saying so
          here beats letting someone find out from the audit trail. */}
      {added.length > 0 && (
        <Alert>
          <AlertDescription>
            Adding{" "}
            <strong>{added.map(prettyNamespace).join(", ")}</strong> also
            grants <strong>read</strong> permissions for it. It will not
            be able to create or change anything until you switch that on
            under <strong>Permissions</strong>.
          </AlertDescription>
        </Alert>
      )}

      {removed.length > 0 && (
        <Alert variant="destructive">
          <AlertDescription>
            Removing{" "}
            <strong>{removed.map(prettyNamespace).join(", ")}</strong>{" "}
            deletes its tools from this agent and revokes every
            permission it holds over it. Your connected account is not
            touched, and re-adding the service grants reads again - not
            whatever you had switched on before.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center gap-3">
        <Button
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
        >
          {save.isPending ? "Saving..." : "Save services"}
        </Button>

        {dirty && !save.isPending && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => setPicked(new Set(services))}
          >
            Cancel
          </Button>
        )}
      </div>

      {services.length === 0 && !dirty && (
        <Alert data-testid="no-services-warning">
          <AlertDescription>
            This agent has no services, so it has no tools and cannot do
            anything yet. Tick one above and save.
          </AlertDescription>
        </Alert>
      )}
    </section>
  );
}
