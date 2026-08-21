"use client";

import {
  Suspense,
  use,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";

import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { MessageInput } from "@/components/chat/message-input";
import { UsageMeter } from "@/components/limits/usage-meter";
import { MessageList } from "@/components/chat/message-list";
import { getAgent } from "@/lib/api/agents";
import { createConversation, listConversations } from "@/lib/api/conversations";
import { useChat } from "@/lib/hooks/use-chat";
import { prettyNamespace } from "@/lib/tools";

export default function ChatPageRoute({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: agentId } = use(params);

  // useSearchParams must sit inside a Suspense boundary. Without it
  // Next refuses to prerender the route and the build fails.
  return (
    <Suspense fallback={<Skeleton className="h-[70vh] w-full" />}>
      <ChatPage agentId={agentId} />
    </Suspense>
  );
}

function ChatPage({ agentId }: { agentId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  // The open conversation lives in the URL, not in state. See the note
  // in ConversationSidebar for why that matters.
  const conversationId = searchParams.get("c");

  const [mobileListOpen, setMobileListOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createFailed, setCreateFailed] = useState(false);

  // A ref, not state: this must NOT trigger a re-render, and it must be
  // readable synchronously so two rapid effect runs cannot both decide
  // they are the first.
  const autoStarted = useRef(false);

  const agent = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => getAgent(agentId),
  });

  const conversations = useQuery({
    queryKey: ["conversations", agentId],
    queryFn: () => listConversations(agentId),
  });

  const openConversation = useCallback(
    (id: string) => {
      // `replace`, not `push`: switching chats should not fill the
      // history stack with one entry per click. Back then leaves the
      // agent, which is what a person expects.
      router.replace(`/agents/${agentId}/chat?c=${id}`);
      setMobileListOpen(false);
    },
    [agentId, router],
  );

  const startNewChat = useCallback(async () => {
    if (creating) return;

    setCreating(true);
    setCreateFailed(false);

    try {
      const created = await createConversation(agentId);

      await queryClient.invalidateQueries({
        queryKey: ["conversations", agentId],
      });

      openConversation(created.id);
    } catch {
      // Recorded rather than swallowed. Without this the auto-start
      // effect below would see "no conversation yet", try again, fail
      // again, and hammer the API in a tight loop for as long as the
      // page stays open.
      setCreateFailed(true);
      toast.error("Could not start a new chat.");
    } finally {
      setCreating(false);
    }
  }, [agentId, creating, openConversation, queryClient]);

  /**
   * Decide which conversation to show when the URL names none.
   *
   * Open the most recent one if there is one, otherwise start a chat.
   * Landing on an empty screen with a button would be one click of
   * friction for nothing - the user came here to talk.
   */
  useEffect(() => {
    if (conversationId || !conversations.isSuccess || creating) return;

    const existing = conversations.data[0];

    if (existing) {
      openConversation(existing.id);
      return;
    }

    // Auto-start at most once per mount, and never again after a
    // failure. The user can still press "+ New chat" themselves.
    if (autoStarted.current || createFailed) return;

    autoStarted.current = true;
    startNewChat();
  }, [
    conversationId,
    conversations.isSuccess,
    conversations.data,
    creating,
    createFailed,
    openConversation,
    startNewChat,
  ]);

  if (createFailed && !conversationId) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex items-center justify-between gap-3">
          <span>Could not start a chat with this agent.</span>
          <Button size="sm" variant="outline" onClick={startNewChat}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  if (agent.isLoading || !conversationId) {
    return <Skeleton className="h-[70vh] w-full" />;
  }

  if (agent.isError || !agent.data) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          Could not open this agent.{" "}
          <Link href="/agents" className="underline">
            Back to agents
          </Link>
        </AlertDescription>
      </Alert>
    );
  }

  const namespaces = agent.data.namespaces ?? [];
  const chatCount = conversations.data?.length ?? 0;

  return (
    <div className="flex h-[calc(100svh-8rem)] gap-4">
      <ConversationSidebar
        agentId={agentId}
        activeId={conversationId}
        onSelect={openConversation}
        onNew={startNewChat}
        className="hidden md:flex"
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
          <div className="min-w-0">
            <Link
              href="/agents"
              className="text-xs text-muted-foreground hover:underline"
            >
              ← Agents
            </Link>

            <h1 className="truncate text-lg font-semibold">
              {agent.data.name}
            </h1>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* The chat list is a sidebar on wide screens and a toggle
                on narrow ones, so a phone is not left without a way to
                reach old conversations. */}
            <Button
              size="sm"
              variant="outline"
              className="md:hidden"
              onClick={() => setMobileListOpen((open) => !open)}
              aria-expanded={mobileListOpen}
            >
              Chats ({chatCount})
            </Button>

            {namespaces.map((ns) => (
              <Badge key={ns} variant="secondary">
                {prettyNamespace(ns)}
              </Badge>
            ))}

            <span className="text-xs text-muted-foreground">
              {agent.data.tool_count} tools
            </span>

            <Button asChild size="sm" variant="ghost">
              <Link href={`/agents/${agentId}/settings`}>Settings</Link>
            </Button>
          </div>
        </header>

        {mobileListOpen && (
          <ConversationSidebar
            agentId={agentId}
            activeId={conversationId}
            onSelect={openConversation}
            onNew={startNewChat}
            className="mt-3 max-h-64 w-full border-b border-r-0 pb-3 pr-0 md:hidden"
          />
        )}

        {/* key={conversationId} REMOUNTS the chat when you switch.
            Without it, useChat keeps the previous conversation's
            messages while the new ones load, and a half-second of the
            wrong transcript is worse than a skeleton. */}
        <ChatSurface
          key={conversationId}
          agentId={agentId}
          agentName={agent.data.name}
          namespaces={namespaces}
          toolCount={agent.data.tool_count}
          conversationId={conversationId}
          // Locked while a new chat is being created.
          //
          // "+ New chat" is asynchronous: it POSTs, then changes the
          // URL, and the URL change REMOUNTS this component (see the
          // `key` above). Anything typed in that gap belongs to the old
          // input and is wiped by the remount - the words vanish and
          // the Send button goes dead, with no explanation.
          //
          // The window is short, but silent text loss is exactly the
          // kind of thing that makes software feel broken.
          disabled={creating}
        />
      </div>
    </div>
  );
}

/**
 * Split out so `useChat` mounts with a conversation id already known.
 *
 * A hook cannot be called conditionally, so if this lived in the parent
 * it would have to run with `null` first and re-run on every id change.
 */
function ChatSurface({
  agentId,
  agentName,
  namespaces,
  toolCount,
  conversationId,
  disabled,
}: {
  agentId: string;
  agentName: string;
  namespaces: string[];
  toolCount: number;
  conversationId: string;
  disabled?: boolean;
}) {
  const { history, historyLoading, live, send, cancel } =
    useChat(conversationId);

  const busy = live !== null && live.phase !== "idle";

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-6">
        {historyLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : history.length === 0 && !live ? (
          <div className="py-16 text-center text-muted-foreground">
            <p className="font-medium text-foreground">
              Ask {agentName} something
            </p>
            <p className="mt-1 text-sm">
              It can use {toolCount} tools across{" "}
              {namespaces.map(prettyNamespace).join(", ") || "no services"}.
            </p>
          </div>
        ) : (
          <MessageList history={history} live={live} agentId={agentId} />
        )}
      </div>

      {/* Only appears once a budget is genuinely under pressure - a
          row of meters above a chat box is noise, and noise is what
          people stop reading before the one message that mattered. */}
      <div className="px-1 pb-1">
        <UsageMeter agentId={agentId} compact />
      </div>

      <MessageInput
        onSend={send}
        onCancel={cancel}
        busy={busy}
        disabled={disabled}
        placeholder={
          disabled ? "Starting a new chat..." : "Ask your agent..."
        }
      />
    </>
  );
}
