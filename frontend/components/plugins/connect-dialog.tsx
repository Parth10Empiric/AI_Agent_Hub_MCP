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

import { connectPlugin } from "@/lib/api/plugins";
import { ApiError } from "@/lib/api/client";
import { prettyNamespace } from "@/lib/tools";

/**
 * Connect a service by pasting an access token.
 *
 * Full OAuth is Phase 5. Pasting a token is enough to build and test
 * everything else, and it keeps the whole flow inside one dialog.
 */
export function ConnectDialog({
  pluginKey,
  open,
  onOpenChange,
}: {
  pluginKey: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
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
