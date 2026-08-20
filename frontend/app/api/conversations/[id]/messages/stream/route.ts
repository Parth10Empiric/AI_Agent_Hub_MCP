import type { NextRequest } from "next/server";

/**
 * A hand-written proxy for the one endpoint that streams.
 *
 * WHY THIS FILE EXISTS
 *
 * Every other /api/* path is forwarded by the `rewrites()` rule in
 * next.config.ts. That rule BUFFERS: it waits for the upstream response
 * to finish before sending anything to the browser. For ordinary JSON
 * that is invisible. For Server-Sent Events it is fatal - measured on
 * this endpoint:
 *
 *     direct to FastAPI      routing t+0.02s ... tool_end t+3.37s
 *                            ... answer_ready t+12.52s
 *
 *     through the rewrite    EVERY event at t+17.24s, all at once
 *
 * The user watches a spinner for seventeen seconds and then the whole
 * turn appears at once - which is exactly the experience SSE exists to
 * avoid, and it makes the live tool timeline pointless.
 *
 * A Route Handler is matched BEFORE the rewrite (rewrites returned from
 * `rewrites()` are "afterFiles", so real routes win), and it can return
 * the upstream body as a stream. Same origin is preserved, so the
 * cookie and CORS reasoning in next.config.ts still holds.
 */

const API_ORIGIN =
  process.env.API_PROXY_ORIGIN ?? "http://127.0.0.1:8077";

// Never statically optimised or cached: the whole point is a live,
// per-request stream.
export const dynamic = "force-dynamic";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  const upstream = await fetch(
    `${API_ORIGIN}/api/conversations/${id}/messages/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",

        // Forwarded explicitly. A new fetch does not inherit the
        // browser's headers, and without these the API sees an
        // anonymous request and answers 401.
        ...(request.headers.get("authorization")
          ? { Authorization: request.headers.get("authorization")! }
          : {}),
        ...(request.headers.get("cookie")
          ? { Cookie: request.headers.get("cookie")! }
          : {}),
      },

      body: await request.text(),

      // Node's fetch will not stream a response body without this hint
      // on some versions; harmless where it is already the default.
      // @ts-expect-error - `duplex` is valid at runtime, not yet typed.
      duplex: "half",

      // If the user closes the tab, stop pulling from upstream. The
      // API's own publisher() still lets the turn finish and persist -
      // it must, because the agent may already have created something.
      signal: request.signal,
    },
  );

  // A non-streaming failure (401, 404, 503) still has a JSON body the
  // client wants to read, so pass it straight through.
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  }

  /**
   * Pump the upstream body through EXPLICITLY.
   *
   * Handing `upstream.body` straight to the Response looks equivalent
   * and is not: measured on this route, the whole turn still arrived in
   * one burst at t+12.9s. Reading each chunk and enqueueing it forces a
   * flush per event, which is what turns the timeline live.
   */
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const reader = upstream.body!.getReader();

      try {
        while (true) {
          const { done, value } = await reader.read();

          if (done) break;

          controller.enqueue(value);
        }
      } catch {
        // The client went away mid-turn. Closing below is the whole
        // cleanup; the API keeps persisting the turn on its side.
      } finally {
        reader.releaseLock();
        controller.close();
      }
    },

    cancel() {
      // The browser closed the connection - stop pulling from upstream.
      upstream.body?.cancel().catch(() => {});
    },
  });

  return new Response(stream, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",

      // Stops any intermediary from holding events back.
      //
      // No `Connection: keep-alive` here. It is a HOP-BY-HOP header:
      // it describes one TCP link, not the message, and a Response is
      // not allowed to set it. Node handles connection reuse itself.
      "Cache-Control": "no-cache, no-transform",

      // nginx honours this to disable its own response buffering. It is
      // ignored elsewhere, and it is the difference between working and
      // silently broken once this sits behind a real proxy.
      "X-Accel-Buffering": "no",
    },
  });
}
