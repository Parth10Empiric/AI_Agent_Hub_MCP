import { api } from "./client";
import type {
  ChatResponse,
  ConversationRead,
  MessagePage,
} from "../types";

export function listConversations(
  agentId: string,
): Promise<ConversationRead[]> {
  return api<ConversationRead[]>(`/api/agents/${agentId}/conversations`);
}

export function createConversation(
  agentId: string,
  title?: string,
): Promise<ConversationRead> {
  return api<ConversationRead>(`/api/agents/${agentId}/conversations`, {
    method: "POST",
    json: { title: title ?? null },
  });
}

export function getConversation(id: string): Promise<ConversationRead> {
  return api<ConversationRead>(`/api/conversations/${id}`);
}

export function renameConversation(
  id: string,
  title: string,
): Promise<ConversationRead> {
  return api<ConversationRead>(`/api/conversations/${id}`, {
    method: "PATCH",
    json: { title },
  });
}

export function deleteConversation(id: string): Promise<void> {
  return api<void>(`/api/conversations/${id}`, { method: "DELETE" });
}

/**
 * One page of history, NEWEST FIRST.
 *
 * That ordering is not a mistake to be fixed in the UI - it is what
 * lets a chat open at the bottom and load older messages as the user
 * scrolls up. The component reverses for display.
 */
export function listMessages(
  conversationId: string,
  options: { limit?: number; cursor?: string } = {},
): Promise<MessagePage> {
  const params = new URLSearchParams();

  if (options.limit) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);

  const query = params.toString();

  return api<MessagePage>(
    `/api/conversations/${conversationId}/messages${query ? `?${query}` : ""}`,
  );
}

/**
 * Send a message and wait for the whole turn (non-streaming).
 *
 * Can legitimately take 30 seconds. Kept alongside the streaming
 * version because it is what tests and scripts want, and because a
 * working non-streaming path proves the stack end to end.
 */
export function sendMessage(
  conversationId: string,
  content: string,
): Promise<ChatResponse> {
  return api<ChatResponse>(
    `/api/conversations/${conversationId}/messages`,
    { method: "POST", json: { content } },
  );
}
