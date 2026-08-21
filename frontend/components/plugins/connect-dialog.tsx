"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";

import { connectPlugin, startOAuth } from "@/lib/api/plugins";
import { ApiError } from "@/lib/api/client";
import { prettyNamespace } from "@/lib/tools";

/**
 * Connect a service.
 *
 * TWO WAYS IN, AND THE FIRST IS BETTER
 *
 *   OAuth   the user approves on the provider's own site, and we never
 *           see a password. Offered when `oauthAvailable` - which the
 *           backend answers from CONFIGURATION, not a hardcoded list,
 *           so the button only appears where it would actually work.
 *
 *   Token   paste a personal access token. Still here on purpose: it
 *           is what CLI use, tests and any provider without OAuth need,
 *           and deleting it would make the dev loop worse for no
 *           security gain.
 *
 * WHY THE OAUTH BUTTON NAVIGATES INSTEAD OF FETCHING
 *
 * A consent screen is a page the user must see and interact with on
 * the provider's domain. No amount of XHR renders one inside this app.
 * So the app asks the backend for the URL with a normal authenticated
 * request - which is why /oauth/start returns JSON rather than a 302 -
 * and then leaves.
 */
export function ConnectDialog({
  pluginKey,
  open,
  onOpenChange,
  oauthAvailable = false,
  oauthScopes = [],
}: {
  pluginKey: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  oauthAvailable?: boolean;
  oauthScopes?: string[];
}) {
  const [credential, setCredential] = useState("");
  const [label, setLabel] = useState("");
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      connectPlugin(pluginKey, {
        credential,
        account_label: label || null,
      }),

    onSuccess: () => {
      toast.success(`${prettyNamespace(pluginKey)} connected`);

      // Refetch the lists that just changed. Invalidating is better
      // than hand-patching the cache: the server may have normalised
      // the label or set a status we did not predict.
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      queryClient.invalidateQueries({ queryKey: ["plugins"] });

      // Cleared so the token does not sit in React state - or in a
      // React DevTools snapshot - after it has been sent.
      setCredential("");
      setLabel("");
      onOpenChange(false);
    },
  });

  const beginOAuth = useMutation({
    mutationFn: () => startOAuth(pluginKey, "/plugins"),

    onSuccess: ({ authorize_url }) => {
      // A full-page navigation, not router.push(). The destination is
      // another origin entirely - Next's router would refuse it, and
      // the user genuinely is leaving the app.
      window.location.href = authorize_url;
    },

    onError: () =>
      toast.error(
        "Could not start the connection. The service may not be set up yet.",
      ),
  });

  const error = mutation.error;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Connect {prettyNamespace(pluginKey)}</DialogTitle>
          <DialogDescription>
            Paste an access token. It is encrypted before it is stored,
            and it is never shown again.
          </DialogDescription>
        </DialogHeader>

        {oauthAvailable && (
          <div className="space-y-3 rounded-lg border p-4">
            <Button
              type="button"
              className="w-full"
              disabled={beginOAuth.isPending}
              onClick={() => beginOAuth.mutate()}
            >
              {beginOAuth.isPending
                ? "Opening..."
                : `Continue with ${prettyNamespace(pluginKey)}`}
            </Button>

            {oauthScopes.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">
                  You will be asked to allow:
                </p>

                {/* Shown HERE, before they leave. A consent screen is
                    something people are trained to click through; this
                    is the page where they still trust the context. */}
                <ul className="space-y-0.5">
                  {oauthScopes.map((scope) => (
                    <li
                      key={scope}
                      className="font-mono text-[11px] text-muted-foreground"
                    >
                      {scope.replace("https://www.googleapis.com/auth/", "")}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-xs text-muted-foreground">
              You approve this on {prettyNamespace(pluginKey)}&apos;s own
              site. We never see your password, and you can revoke it
              there at any time.
            </p>
          </div>
        )}

        {oauthAvailable && (
          <div className="flex items-center gap-3">
            <span className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">
              or paste a token
            </span>
            <span className="h-px flex-1 bg-border" />
          </div>
        )}

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="credential">Access token</Label>
            <Input
              id="credential"
              // type="password" so it is not readable over a shoulder
              // or in a screen share while it is being pasted.
              type="password"
              autoComplete="off"
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              placeholder="ghp_..."
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="label">Account name (optional)</Label>
            <Input
              id="label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="you@example.com"
            />
            <p className="text-xs text-muted-foreground">
              Only to help you recognise this connection later.
            </p>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>
                {error instanceof ApiError && error.status === 409
                  ? "This service is already connected."
                  : error instanceof Error
                    ? error.message
                    : "Could not connect. Check the token and try again."}
              </AlertDescription>
            </Alert>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>

            <Button
              type="submit"
              // Minimum 8 characters, matching the backend's own rule,
              // so an obviously-empty paste is caught before a request.
              disabled={credential.trim().length < 8 || mutation.isPending}
            >
              {mutation.isPending ? "Connecting..." : "Connect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
