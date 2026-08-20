"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  deleteConversation,
  listConversations,
  renameConversation,
} from "@/lib/api/conversations";
import { formatRelative } from "@/lib/tools";
import { cn } from "@/lib/utils";
import type { ConversationRead } from "@/lib/types";

/**
 * Every chat this agent has had.
 *
 * WHY THIS EXISTS
 *
 * Before it, "New chat" was a one-way door: it opened a fresh
 * conversation and there was no way back to the previous one. The
 * history was in the database the whole time - there was simply no
 * door to it.
 *
 * WHY THE SELECTED CHAT LIVES IN THE URL
 *
 * `/agents/{id}/chat?c={conversationId}`
 *
 * Keeping it in React state instead would mean:
 *   - refreshing the page drops you back into the newest chat
 *   - the browser Back button walks out of the agent entirely rather
 *     than back to the chat you were just reading
 *   - you cannot bookmark or share a conversation
 *
 * The URL is the one piece of state the browser already knows how to
 * save, restore and share. Anything a person would expect Back to undo
 * belongs there.
 */
export function ConversationSidebar({
  agentId,
  activeId,
  onSelect,
  onNew,
  className,
}: {
  agentId: string;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const [renaming, setRenaming] = useState<ConversationRead | null>(null);
  const [deleting, setDeleting] = useState<ConversationRead | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  const conversations = useQuery({
    queryKey: ["conversations", agentId],
    queryFn: () => listConversations(agentId),
  });

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      renameConversation(id, title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations", agentId] });
      setRenaming(null);
    },
    onError: () => toast.error("Could not rename this chat."),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: (_result, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ["conversations", agentId] });
      setDeleting(null);

      // Deleting the chat you are reading has to move you somewhere.
      // Staying put would leave the page pointing at a conversation the
      // server will now 404 on.
      if (deletedId === activeId) {
        const next = (conversations.data ?? []).find((c) => c.id !== deletedId);

        if (next) onSelect(next.id);
        else onNew();
      }
    },
    onError: () => toast.error("Could not delete this chat."),
  });

  const items = conversations.data ?? [];

  return (
    <aside
      className={cn(
        "flex w-60 shrink-0 flex-col gap-2 border-r pr-3",
        className,
      )}
    >
      <Button
        size="sm"
        variant="outline"
        className="w-full justify-start"
        onClick={onNew}
      >
        + New chat
      </Button>

      {/* min-h-0 so this scrolls instead of stretching the page. */}
      <nav
        aria-label="Conversations"
        className="min-h-0 flex-1 space-y-0.5 overflow-y-auto"
      >
        {conversations.isLoading ? (
          <div className="space-y-2 pt-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : items.length === 0 ? (
          <p className="px-2 py-4 text-xs text-muted-foreground">
            No chats yet. Ask something to start one.
          </p>
        ) : (
          items.map((conversation) => {
            const isActive = conversation.id === activeId;

            return (
              <div
                key={conversation.id}
                className={cn(
                  "group flex items-center gap-1 rounded-md pr-1",
                  isActive ? "bg-muted" : "hover:bg-muted/60",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelect(conversation.id)}
                  // aria-current is how a screen reader conveys "this is
                  // the one you are reading". Background colour alone
                  // does not say it.
                  aria-current={isActive ? "page" : undefined}
                  className="min-w-0 flex-1 px-2 py-1.5 text-left"
                >
                  <span className="block truncate text-sm">
                    {conversation.title || "New chat"}
                  </span>

                  <span className="block truncate text-[11px] text-muted-foreground">
                    {conversation.last_message_at
                      ? formatRelative(conversation.last_message_at)
                      : "Empty"}
                  </span>
                </button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      // Hidden until hover or keyboard focus. focus:opacity-100
                      // matters: without it the control is unreachable by
                      // keyboard, because a keyboard user never hovers.
                      className="size-7 shrink-0 p-0 opacity-0 transition-opacity focus:opacity-100 group-hover:opacity-100"
                      aria-label={`Options for ${conversation.title || "this chat"}`}
                    >
                      <span aria-hidden>⋯</span>
                    </Button>
                  </DropdownMenuTrigger>

                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onClick={() => {
                        setRenaming(conversation);
                        setDraftTitle(conversation.title ?? "");
                      }}
                    >
                      Rename
                    </DropdownMenuItem>

                    <DropdownMenuItem
                      variant="destructive"
                      onClick={() => setDeleting(conversation)}
                    >
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            );
          })
        )}
      </nav>

      <Dialog
        open={Boolean(renaming)}
        onOpenChange={(open) => !open && setRenaming(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename chat</DialogTitle>
          </DialogHeader>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (renaming && draftTitle.trim()) {
                rename.mutate({ id: renaming.id, title: draftTitle.trim() });
              }
            }}
          >
            <Input
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
              maxLength={255}
              autoFocus
              aria-label="Chat name"
            />

            <DialogFooter className="mt-4">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setRenaming(null)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={!draftTitle.trim()}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(open) => !open && setDeleting(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this chat?</DialogTitle>
            <DialogDescription>
              The messages and the record of which tools ran are removed
              for good. This cannot be undone.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            {/* Cancel first in the DOM so it takes focus by default. */}
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleting && remove.mutate(deleting.id)}
              disabled={remove.isPending}
            >
              {remove.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  );
}
