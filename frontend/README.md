# Agent Hub — web app

Next.js 15 (app router), React 19, Tailwind v4, shadcn/ui, TanStack
Query. The backend is FastAPI, in the parent directory; see the [root
README](../README.md) for what the product is and what it looks like.

```bash
npm install
npm run dev          # http://localhost:3000 — needs the API on :8077
```

| Script | |
|---|---|
| `npm run dev` | Development server |
| `npm run build` / `start` | Production build and serve |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint |
| `npm run gen:api` | Regenerate `lib/api/schema.d.ts` from the live API's OpenAPI document |
| `npm run test:e2e` | Playwright, against the real stack |
| `npm run capture` | Re-render the screenshots in `../docs/media` |

---

## Four things that are not obvious

**Everything is one origin.** `next.config.ts` rewrites `/api/*` to
FastAPI, so the browser only ever talks to its own origin. That is not
convenience — the refresh token lives in an httpOnly cookie, and talking
to `:8077` directly would mean `SameSite=None`, which requires `Secure`,
which requires TLS in local development, plus CORS preflight on every
mutating request. Behind the rewrite, `SameSite=Lax` just works, and
development and production behave identically.

**The streaming endpoint has its own hand-written proxy.** The rewrite
*buffers*: it waits for the whole upstream response before sending
anything. For JSON that is invisible; for Server-Sent Events it means
every event in a turn arrives at once, seventeen seconds in, which makes
the live tool timeline pointless.
[`app/api/conversations/[id]/messages/stream/route.ts`](app/api/conversations/%5Bid%5D/messages/stream/route.ts)
is a Route Handler — matched *before* the rewrite — that pumps the
upstream body through chunk by chunk.

**The access token is a module-level variable.** Not `localStorage`,
which any script on the page can read. It dies with the tab, and on
reload the app trades the httpOnly refresh cookie for a new one via
`/api/auth/refresh`. The session survives; the credential never passes
through readable storage. See [`lib/api/tokens.ts`](lib/api/tokens.ts).

**`lib/types.ts` is not hand-written.** It aliases
`lib/api/schema.d.ts`, which `npm run gen:api` generates from the live
OpenAPI document. Two hand-maintained copies of the same shape always
drift, and the drift is silent — the backend renames `risk_level`, the
frontend still compiles, and a badge renders `undefined` in production.
If you find yourself writing `interface Agent { ... }`, regenerate
instead.

---

## Layout

```
app/
  (auth)/        login, register
  (app)/         dashboard, plugins, agents, executions, security
                 agents/[id]/{chat,permissions,settings}
  api/           the one hand-written route: the SSE proxy
components/
  chat/          message list, composer, markdown, tool timeline
  approvals/     the approval prompt
  ui/            shadcn primitives
lib/
  api/           one typed wrapper per endpoint, all through api()
  hooks/         use-chat (the turn state machine), use-sse, use-session
```

The two components worth reading first are
[`components/approvals/approval-request.tsx`](components/approvals/approval-request.tsx)
and [`lib/hooks/use-chat.ts`](lib/hooks/use-chat.ts). Between them they
hold the whole model of what a turn is: what it looks like while it runs,
what happens when it stops to ask a question, and why an answered
question stays on screen showing its answer rather than disappearing.

---

## Tests

```bash
npm run test:e2e
```

Against the **real** backend, not mocks — the suite exists to prove the
whole product works together, and a mock cannot prove that. It needs
`uvicorn api.main:app --port 8077` running. Playwright builds and serves
the app itself, on port 3100, from `.next-e2e/` so it cannot disturb a
dev server you have open.

`tests/e2e/approval.spec.ts` is the one that matters: it asserts that
ticking a write tool is *not* enough without the scope, that granting the
scope turns the refusal into a prompt, and that the prompt shows the real
argument **values** — because a prompt showing only argument names trains
people to approve without reading, and then a prompt-injected
`attacker@evil.com` gets approved too.

## Screenshots

```bash
npm run capture
```

Rebuilds every image in `../docs/media` from the real production bundle,
served against the scripted backend in
[`tests/demo/mock-api.mjs`](tests/demo/mock-api.mjs). Run it after any
change to the screens the root README embeds. It needs nothing running —
no database, no model, no credentials.

It is not a test and asserts nothing; its `expect` calls are waits, so
that an unchanged UI produces byte-identical images and a regenerated
file is a real diff.
