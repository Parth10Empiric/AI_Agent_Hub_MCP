"use client";

import { getAccessToken } from "@/lib/api/tokens";
import { API_BASE } from "@/lib/api/client";

/**
 * Reading a Server-Sent Events stream from a POST request.
 *
 * WHY NOT `new EventSource(url)`
 *
 * EventSource is the built-in way to consume SSE, and it cannot be used
 * here for two independent reasons:
 *
 *   1. It only ever issues GET. Our endpoint is
 *      POST /api/conversations/{id}/messages/stream, because the
 *      message body is the request.
 *   2. It cannot set headers, so there is no way to send
 *      `Authorization: Bearer ...`. The usual workaround is putting the
 *      token in the query string, which then lands in server logs,
 *      proxy logs and browser history. That is not acceptable for a
 *      credential.
 *
 * So we use fetch, which supports both, and parse the SSE framing
 * ourselves. It is about forty lines, and they are below.
 *
 * WHAT WE GIVE UP, AND WHY IT IS FINE
 *
 * EventSource reconnects automatically. fetch does not. But automatic
 * reconnection is wrong for this endpoint anyway: an agent turn is not
 * idempotent - it may have created a GitHub issue - so silently
 * re-sending the message on a dropped connection could do the work
 * twice. A dropped turn is surfaced to the user, who decides.
 */

/** The event vocabulary the backend actually emits. */
export type StreamEventName =
  | "routing"
  | "round_start"
  | "tool_start"
  | "tool_end"
  | "escalation"
  | "answer_ready"
  | "done"
  | "error";

export interface StreamEvent {
  event: StreamEventName | string;
  data: Record<string, unknown>;
}

export interface StreamOptions {
  signal?: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}

/**
 * POST a message and yield each SSE event as it arrives.
 *
 * Resolves when the stream closes. Throws on a non-2xx response, so the
 * caller can distinguish "the request was rejected" (401, 404, 503)
 * from "the turn itself failed", which arrives as an `error` event.
 */
export async function streamTurn(
  conversationId: string,
  content: string,
  { signal, onEvent }: StreamOptions,
): Promise<void> {
  const token = getAccessToken();

  const response = await fetch(
    `${API_BASE}/api/conversations/${conversationId}/messages/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      credentials: "include",
      body: JSON.stringify({ content }),
      signal,
    },
  );

  if (!response.ok || !response.body) {
    // Read the body before throwing - it usually carries the reason,
    // e.g. 503 "The tool server is not available right now."
    let detail = `Stream failed with status ${response.status}`;

    try {
      const body = await response.json();
      detail = body?.detail ?? body?.error?.message ?? detail;
    } catch {
      // No JSON body. The status alone is the whole message.
    }

    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  // Bytes that arrived but do not yet form a complete event. A chunk
  // boundary can fall ANYWHERE - including the middle of a UTF-8
  // character or halfway through a JSON payload - so nothing is parsed
  // until a blank line proves the event is complete.
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      // stream: true keeps a multi-byte character split across two
      // chunks from being decoded as two broken characters.
      buffer += decoder.decode(value, { stream: true });

      // SSE separates events with a BLANK LINE. Normalise \r\n first:
      // some proxies rewrite line endings, and splitting on "\n\n"
      // alone would then never match and the UI would receive nothing.
      const normalised = buffer.replace(/\r\n/g, "\n");
      const frames = normalised.split("\n\n");

      // The last piece is either an incomplete frame or an empty
      // string. Either way it stays in the buffer for the next chunk.
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const parsed = parseFrame(frame);
        if (parsed) onEvent(parsed);
      }
    }
  } finally {
    // Releases the underlying connection. Skipping this leaks a socket
    // per abandoned turn, and browsers cap concurrent connections per
    // origin - so after six abandoned turns the app would simply hang.
    reader.releaseLock();
  }
}

function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    // Comment/keepalive frames start with ":". Servers send them to
    // stop proxies closing an idle connection; they carry no payload.
    if (!line || line.startsWith(":")) continue;

    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      // Per the spec a single event may carry several data: lines,
      // which are joined with newlines.
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) return null;

  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    // Malformed JSON from the server should not tear down the whole
    // stream. Skip the frame; the rest of the turn still arrives.
    return null;
  }
}
