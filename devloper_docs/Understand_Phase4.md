# Understanding Phase 4 — The Frontend

> **Who this is for:** someone who has never built a web frontend
> before, and anyone (including you, in six months) who needs to fix a
> bug, add a screen, or explain to a client why this thing is built the
> way it is.
>
> By the end you should understand *what* we built, *why* every piece
> exists, *how* the tricky parts work, and *where to look* when
> something breaks.
>
> Every technical word is explained the first time it appears. The
> language is kept simple on purpose.

---

## Table of contents

1. [What Phase 4 is, in plain words](#1-what-phase-4-is-in-plain-words)
2. [The big picture](#2-the-big-picture)
3. [File map — what lives where](#3-file-map--what-lives-where)
4. [Part A — Setup and the type pipeline (4.1)](#part-a--setup-and-the-type-pipeline-41)
5. [Part B — Tokens in the browser (4.2)](#part-b--tokens-in-the-browser-42)
6. [Part C — The React ideas you actually need](#part-c--the-react-ideas-you-actually-need)
7. [Part D — The screens (4.3 – 4.8)](#part-d--the-screens-43--48)
8. [Part E — Streaming (4.9)](#part-e--streaming-49)
9. [Part F — The backend piece that was missing](#part-f--the-backend-piece-that-was-missing)
10. [Part G — Testing (4.10)](#part-g--testing-410)
11. [Following one message end to end](#11-following-one-message-end-to-end)
12. [Every bug we hit, and what it taught](#12-every-bug-we-hit-and-what-it-taught)
13. [Troubleshooting guide](#13-troubleshooting-guide)
14. [How to add things](#14-how-to-add-things)
15. [Known gaps](#15-known-gaps)
16. [Glossary](#16-glossary)

---

## 1. What Phase 4 is, in plain words

### Where we started

After Phase 3 we had a **working HTTP API**. You could do everything
the product does — but only like this:

```bash
curl -X POST localhost:8077/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"..."}'
```

That is fine for you. It is useless for a customer.

### What Phase 4 changes

Phase 4 is the part a person actually touches. The test of whether it
worked is written in `phases_doc/Phase4.md` and it is a good one:

> A user should think **"connect GitHub → create an agent → ask it
> about my repository"** and never once encounter the words *MCP*,
> *tool schema* or *namespace*.

So every decision in this phase answers one question: **does this make
the machinery invisible?**

### The size of it

```text
50 source files          (not counting 21 shadcn UI primitives)
~6,760 lines             of TypeScript and TSX (excluding generated types)
12 pages                 login, register, dashboard, plugins,
                         plugin detail, agents, new agent, chat,
                         settings, activity, plus two redirects
1 route handler          the SSE proxy (see Part E)
27 end-to-end tests      all passing against the real stack
```

### What "frontend" means here

Three different jobs that people lump together:

```text
The pages you see          React components rendered as HTML
The talking-to-the-API     one fetch wrapper, typed, with auth
The state in between       what is loaded, loading, or failed
```

Most frontend bugs are in the third one. That is why a large part of
this document is about state.

---

## 2. The big picture

```text
                        Your browser
                             │
                             ▼
              ┌──────────────────────────────┐
              │      Next.js (port 3000)     │
              │                              │
              │  ┌────────────────────────┐  │
              │  │  React pages           │  │
              │  │  app/(auth)/...        │  │
              │  │  app/(app)/...         │  │
              │  └───────────┬────────────┘  │
              │              │               │
              │      lib/api/client.ts       │
              │      (the ONE fetch)         │
              │              │               │
              │  ┌───────────▼────────────┐  │
              │  │ /api/* is proxied      │  │
              │  │  - normal calls:       │  │
              │  │    rewrite (fallback)  │  │
              │  │  - the SSE stream:     │  │
              │  │    a real route handler│  │
              │  └───────────┬────────────┘  │
              └──────────────┼───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │     FastAPI (port 8077)      │
              │        Phase 3, unchanged    │
              └──────────────┬───────────────┘
                             ▼
                   AgentEngine → MCP → PostgreSQL
```

### The one rule that shapes everything

**The browser only ever talks to one address.**

Pages are on `localhost:3000`. The API is on `localhost:8077`. If the
browser called the API directly, those are two different *origins*, and
browsers treat that as a security boundary. You would then have to deal
with:

- cookies needing `SameSite=None`, which needs `Secure`, which needs
  HTTPS even on your laptop
- a "preflight" request before every write
- CORS headers configured on the API

Instead Next forwards `/api/*` to FastAPI on the server side. The
browser sees only `localhost:3000/api/...` — its own origin. No CORS,
no HTTPS in development, and development behaves like production.

This single decision comes back in [Part B](#part-b--tokens-in-the-browser-42)
(cookies) and again in [Part E](#part-e--streaming-49), where it caused
the hardest bug of the phase.

---

## 3. File map — what lives where

### Configuration

```text
frontend/next.config.ts        the /api proxy rule
frontend/playwright.config.ts  how end-to-end tests run
frontend/package.json          scripts: dev, build, gen:api, test:e2e
frontend/components.json       shadcn settings
```

### Talking to the API

```text
lib/api/schema.d.ts       GENERATED. 2,204 lines. Never edit by hand.
lib/types.ts              readable names for the generated types
lib/api/tokens.ts         the access token, in memory
lib/api/client.ts         the ONE fetch: auth, refresh, errors
lib/api/auth.ts           register / login / logout / me / restore
lib/api/plugins.ts        catalogue, connections, connect, disconnect
lib/api/agents.ts         CRUD + tool selection
lib/api/conversations.ts  list, create, rename, delete, history, send
lib/api/executions.ts     activity list and stats
```

### State and logic

```text
lib/hooks/use-session.tsx  who is logged in, for the whole app
lib/hooks/use-sse.ts       reading a Server-Sent Events stream
lib/hooks/use-chat.ts      history + live turn + sending
lib/validation.ts          Zod rules for the forms
lib/tools.ts               how the UI words and colours tools
```

### Screens

```text
app/layout.tsx                    the outer HTML shell (server)
app/providers.tsx                 client providers (query + session)
app/page.tsx                      "/" decides where to send you

app/(auth)/layout.tsx             centred card shell
app/(auth)/login/page.tsx
app/(auth)/register/page.tsx

app/(app)/layout.tsx              sidebar shell + the auth gate
app/(app)/dashboard/page.tsx
app/(app)/plugins/page.tsx
app/(app)/plugins/[key]/page.tsx
app/(app)/agents/page.tsx
app/(app)/agents/new/page.tsx     the 4-step wizard
app/(app)/agents/[id]/page.tsx    redirect to chat
app/(app)/agents/[id]/chat/page.tsx
app/(app)/agents/[id]/settings/page.tsx
app/(app)/executions/page.tsx
```

### Components

```text
components/agents/tool-picker.tsx      the trust screen
components/chat/message-list.tsx       the conversation
components/chat/message-input.tsx      the box you type in
components/chat/tool-timeline.tsx      what the agent did
components/chat/approval-notice.tsx    what it was NOT allowed to do
components/chat/markdown.tsx           renders the agent's formatting
components/chat/conversation-sidebar.tsx  every chat this agent has had
components/plugins/connect-dialog.tsx  paste a token
components/ui/*                        21 shadcn primitives
```

### The one server-side file

```text
app/api/conversations/[id]/messages/stream/route.ts
```

This is a hand-written proxy for the streaming endpoint. It exists
because of a real bug. See [Part E](#part-e--streaming-49).

### Tests

```text
tests/e2e/helpers.ts        register a fresh user, connect a service
tests/e2e/auth.spec.ts      6 tests
tests/e2e/plugins.spec.ts   3 tests
tests/e2e/agents.spec.ts    4 tests
tests/e2e/chat.spec.ts      2 tests
tests/e2e/approval.spec.ts  1 test
tests/e2e/executions.spec.ts 3 tests
tests/e2e/failure.spec.ts   2 tests
tests/e2e/rendering.spec.ts 3 tests
tests/e2e/conversations.spec.ts 3 tests
```

---

# Part A — Setup and the type pipeline (4.1)

## A1. The stack, and why each piece is there

```text
Next.js 15        the framework: routing, server rendering, build
React 19          the UI library underneath it
TypeScript        types checked before the code runs
Tailwind CSS 4    styling with class names
shadcn/ui         accessible components you own the code for
TanStack Query    remembers what was fetched, and when to refetch
Zod               checks data while the program runs
Playwright        drives a real browser for tests
```

Two of these deserve a sentence on *why*, because the choice is not
obvious.

**shadcn/ui is not a library you install.** It copies component source
files into `components/ui/`. You own them. When a client asks for a
different button, you edit the file — you do not fight a library's
theming system. The trade is that you must maintain them yourself.

**TanStack Query is not a data-fetching library.** `fetch` already
fetches. Query is a *cache*. It remembers that `["agents"]` was fetched
30 seconds ago, so moving between pages does not refetch it. It also
tracks loading and error states so every screen does not reinvent them.

## A2. The most important rule in Phase 4: generate the types

`phases_doc/Phase4.md` says it plainly:

> Keep `lib/types.ts` generated, not hand-written.

Here is what that means in practice.

FastAPI already publishes a full description of itself:

```bash
curl http://127.0.0.1:8077/openapi.json
```

That document lists every URL, every field, and every type. A tool
called `openapi-typescript` turns it into TypeScript:

```bash
npm run gen:api
# reads  http://127.0.0.1:8077/openapi.json
# writes lib/api/schema.d.ts   (2,204 lines)
```

`lib/types.ts` then just gives those deep names friendlier ones:

```ts
import type { components } from "./api/schema";

type Schemas = components["schemas"];

export type AgentDetail  = Schemas["AgentDetail"];
export type ToolSummary  = Schemas["ToolSummary"];
export type ChatResponse = Schemas["ChatResponse"];
```

### Why this matters more than it sounds

Imagine you hand-write this instead:

```ts
interface Agent {
  id: string;
  name: string;
  risk_level: string;   // ← you typed this from memory
}
```

Six weeks later someone renames `risk_level` to `risk` in Python. Your
TypeScript still compiles perfectly. Nothing warns you. A badge on the
tool picker quietly renders `undefined` in production, and you find out
from a client.

With generated types that rename becomes a **build error**, before the
code ever runs.

### It caught real mistakes twice in this phase

Both of these were *my* wrong assumptions, caught before a single
request was sent:

```text
1. I assumed Operation had six values
   (read, write, delete, access_control, execute, unknown)

   The real enum in agent/schemas.py has FOUR:
   read, write, delete, admin

2. I assumed the tool-selection payload was a LIST:
   { tools: [ { tool_name, enabled } ] }

   AgentToolsUpdate.tools is a MAP keyed by tool name:
   { tools: { "github_create_issue": { enabled: true } } }
```

The second one would have been a 422 error at runtime with a confusing
message. The type checker said it in one line.

> **The habit to build:** if you catch yourself typing
> `interface Agent {`, stop and run `npm run gen:api` instead.

### The one wrinkle

`openapi-typescript` marks any field that has a *default value* as
**required**. That is right for a response (the server always sends it)
and wrong for a request (a default exists precisely so you can leave it
out). `AgentCreate.model` and `.temperature` have defaults, so:

```ts
// lib/api/agents.ts
export type AgentCreatePayload = Omit<AgentCreate, "model" | "temperature"> &
  Partial<Pick<AgentCreate, "model" | "temperature">>;
```

This keeps the model name owned by the backend instead of hardcoded in
the UI.

## A3. The proxy, and why it is not just convenience

`next.config.ts`:

```ts
async rewrites() {
  return {
    fallback: [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
    ],
  };
}
```

`API_ORIGIN` is **not** `NEXT_PUBLIC_*`. That prefix is what makes a
variable visible to the browser. This one is read only by the Next
server, so in production the API can sit on a private network the
browser cannot reach at all.

> **The word `fallback` is load-bearing.** It caused the hardest bug of
> the phase. Full story in [Part E](#e3-the-bug-that-took-longest-sse-buffering).

---

# Part B — Tokens in the browser (4.2)

This is the part of the frontend with real security consequences.

## B1. Three places to put a token, and what each one means

```text
localStorage   Any script on the page can read it. One bad npm
               package, one injected <script>, and the attacker has a
               token they can use from their own machine for as long
               as it lives.

memory         A normal JavaScript variable. There is no API to list
               it. It dies when the tab closes.

httpOnly       The browser stores it and sends it automatically.
  cookie       JavaScript cannot read it AT ALL.
```

Our choice:

```text
access token   (15 minutes)  →  memory
refresh token  (30 days)     →  httpOnly cookie
```

The short-lived one lives in the riskier place. The long-lived one
lives where no script can touch it. That is the whole idea.

`lib/api/tokens.ts` is deliberately tiny:

```ts
let accessToken: string | null = null;

export function getAccessToken() { return accessToken; }
export function setAccessToken(token: string | null) { accessToken = token; }
```

### "But it is lost when I refresh the page!"

Yes. That is the **feature**, not the bug.

On load, `restoreSession()` asks the server for a new access token. The
browser attaches the refresh cookie automatically. A new token comes
back. The session survives — and the credential never passed through
storage any script could read.

We verified the cookie the server actually sets:

```text
set-cookie: refresh_token=<hidden>; HttpOnly; Max-Age=2592000;
            Path=/api/auth; SameSite=lax
```

`HttpOnly` ✓, scoped to `/api/auth` ✓, and `SameSite=lax` works
*because* of the same-origin proxy. The backend also sets
`secure=settings.is_production`, so the `Secure` flag appears
automatically when you deploy.

## B2. One fetch for the whole app

Every request goes through `api()` in `lib/api/client.ts`. Not because
it saves typing, but because these decisions should be made **once**:

- attach the `Authorization` header
- refresh the token on 401 and retry
- turn an error body into a useful message
- handle `204 No Content` (calling `.json()` on an empty body throws)

Scatter raw `fetch` calls through components and each one is a chance
to forget the header.

## B3. The refresh stampede

This is the subtle one, and it is worth understanding because it bites
people in week three.

```text
The dashboard opens and fires three requests at once:
  GET /api/agents
  GET /api/plugins
  GET /api/plugins/connections

The access token expired one second ago.
All three get 401 at the same moment.
All three call /api/auth/refresh.
```

Your backend **rotates** refresh tokens — using one invalidates it and
issues a new one (Phase 3.4, and it is the right design: it is how you
detect a stolen token). So:

```text
call 1  →  succeeds, token rotated
call 2  →  the token it sent is now dead  →  401
call 3  →  same  →  401
```

Two requests fail and the user is thrown to the login page, with a
perfectly valid session.

The fix is to store the **promise**, not a boolean flag:

```ts
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken() {
  if (refreshInFlight) return refreshInFlight;   // ← join the existing one
  refreshInFlight = (async () => { ... })();
  return refreshInFlight;
}
```

The first 401 starts the refresh. The other two `await` that same
promise. One network call, one rotation, three retried requests.

> A boolean flag would not work: the second caller would see "a refresh
> is happening" and have nothing to wait for.

And the retry happens **exactly once**:

```ts
if (response.status === 401 && !_isRetry) { ... }
```

An unconditional retry against an endpoint that keeps returning 401 is
a denial-of-service attack on your own backend.

## B4. Two real bugs in this area

### Bug: you could not reach the register page

Symptom: open `/register` while logged out, and you land on `/login`.

Cause: on first load the app calls `/api/auth/refresh` to see whether a
session exists. For a visitor the answer is `401` — which is the
*normal, expected* answer. But the client treated any failed refresh as
"your session ended" and fired the redirect.

Fix — ask whether there was a session to lose:

```ts
const token = getAccessToken();
const hadSession = token !== null;      // captured BEFORE the request
...
if (hadSession) {
  notifySessionEnded();                 // only then is it a real logout
}
```

`hadSession` must be captured *before* the request, because a failed
refresh clears the token and afterwards the two cases look identical.

### Bug: logging out did nothing

Symptom: click "Sign out", stay on the dashboard.

Cause: the frontend sent `POST /api/auth/logout` with **no body**. The
endpoint declares `payload: RefreshRequest` (it accepts the token in
the body for CLI clients), so FastAPI rejected it with `422` before any
logout logic ran. `api()` threw, and the throw happened *before*
`router.replace("/login")`.

Two fixes, both needed:

```ts
// 1. Send the body the endpoint expects.
await api<void>("/api/auth/logout", { method: "POST", json: {} });

// 2. Never let a server problem block a local sign-out.
} catch {
  // Swallowed on purpose: "Sign out" must always sign you out of
  // THIS browser, even if the server is unreachable.
} finally {
  clearAccessToken();
}
```

> **The lesson:** an endpoint that takes a body requires a body, even
> an empty one. The same mistake had already appeared on
> `/api/auth/refresh` during Phase 3 testing. When you see a 422 on a
> request you thought had no fields, check for a declared body model.

---

# Part C — The React ideas you actually need

You cannot read the rest of this document without these four.

## C1. Server components vs client components

Next.js 15 App Router has two kinds of component.

```text
SERVER COMPONENT  (the default)
  Runs on the server. Produces HTML. Sends it.
  CAN     fetch data directly with await
  CANNOT  useState, useEffect, onClick
  Its code is never sent to the browser.

CLIENT COMPONENT  ("use client" at the top of the file)
  Sent to the browser and made interactive.
  CAN     state, effects, event handlers
  Everything it imports is also sent to the browser.
```

That last line is the practical rule. If you put `"use client"` at the
top of `app/layout.tsx`, your **entire application** gets pulled into
the JavaScript bundle.

So the root layout stays a server component, and the interactive parts
are pushed one level down into `app/providers.tsx`:

```tsx
// app/layout.tsx — a SERVER component, note no "use client"
export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>   {/* client starts here */}
      </body>
    </html>
  );
}
```

Authentication is memory-resident and interactive, so it is
unavoidably a client concern. `use-session.tsx` will always be a client
component.

## C2. Route groups — folders in brackets

```text
app/(auth)/login/page.tsx      →  /login
app/(app)/dashboard/page.tsx   →  /dashboard
```

A folder in **parentheses** groups routes without appearing in the URL.
This is how the app has two completely different shells — a centred
card for signed-out pages, a sidebar for signed-in ones — with no
`if (loggedIn)` inside a single layout.

A folder in **square brackets** is a parameter:

```text
app/(app)/agents/[id]/chat/page.tsx   →  /agents/<any-id>/chat
```

In Next 15 those parameters arrive as a **Promise**, because a route
can begin rendering before they are known:

```tsx
export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);     // `use` unwraps it in a client component
```

## C3. The auth gate, and why it lives in a layout

`app/(app)/layout.tsx` wraps every signed-in page:

```tsx
useEffect(() => {
  if (!isLoading && !user) {
    router.replace("/login");
  }
}, [isLoading, user, router]);
```

Two things to notice.

**Why a layout?** Because it wraps everything beneath it, one check
protects every current and future page in the group. Put the check in
each page and protecting a new page becomes something you must
remember — and one day will not.

**Why `!isLoading`?** On the very first render `user` is `null` simply
because the refresh request has not come back yet. Redirect on that and
you bounce every logged-in user to `/login` on every page refresh. The
app needs three states, not two:

```text
isLoading true    we do not know yet     → show a skeleton
user null         definitely signed out  → redirect
user set          signed in              → render
```

> **This gate is not security.** It hides UI. Anyone can bypass
> client-side routing. The real enforcement is the backend returning
> 404 for another user's agent — which Phase 3's milestone test 5
> proved it does.

## C4. State traps that cost real time

### Trap 1 — mutating instead of replacing

```ts
// WRONG — the checkbox appears to do nothing
selected.add(name);
onChange(selected);
```

React compares by identity. The Set is the *same object*, so React sees
no change and skips the re-render.

```ts
// RIGHT
const copy = new Set(selected);
copy.add(name);
onChange(copy);
```

### Trap 2 — an effect that triggers itself

```ts
useEffect(() => {
  setDraft({ ...draft, tools: reads });   // ← runs forever
}, [availableTools]);
```

`setDraft` causes a re-render, the re-render creates a new
`availableTools` array, the new array retriggers the effect. Two
defences, both used in the wizard:

```ts
// 1. Depend on a STABLE value — a string, not a fresh array.
const readToolNames = availableTools.filter(t => t.read_only)
                                    .map(t => t.name).join(",");

// 2. Compare before setting.
setDraft(d => d.tools.join(",") === readToolNames ? d : { ...d, tools: reads });
```

Returning the *same* object from a state setter tells React nothing
changed.

### Trap 3 — a useMemo whose dependencies lie

I originally wrote this in the wizard:

```ts
const availableTools = useMemo(() => { ... },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [pluginDetails.map(q => q.data?.key).join(","), ...]);
```

That `eslint-disable` is a warning sign. `useQueries` returns a new
array every render, so the memo would recompute anyway — the only thing
it added was a dependency list that was not telling the truth.

It was replaced with a plain calculation. Flattening 60 objects is
cheaper than the bug a lying dependency list eventually causes.

> **Rule of thumb:** reach for `useMemo` when you have measured a
> problem, not by default.

## C5. TanStack Query in one page

```tsx
const agents = useQuery({ queryKey: ["agents"], queryFn: listAgents });

agents.isLoading   // first load
agents.data        // the result, or undefined
agents.isError     // it failed
```

The **query key** is the cache key. Two components asking for
`["agents"]` share one request. After a change you tell the cache it is
stale:

```ts
queryClient.invalidateQueries({ queryKey: ["connections"] });
```

Invalidating is better than patching the cache by hand, because the
server may have normalised something you did not predict.

Two settings in `app/providers.tsx` are worth knowing:

```ts
retry: (failureCount, error) => {
  // Never retry something the server answered definitively. A 401,
  // 403, 404 or 422 returns the same answer three times; retrying
  // only delays the error the user needs to see.
  if (error instanceof ApiError && error.status < 500) return false;
  return failureCount < 2;
},

mutations: { retry: false },
// A retried POST can create two agents, or send a message twice.
```

And the client is created inside `useState`:

```ts
const [queryClient] = useState(() => new QueryClient({ ... }));
```

A module-level `new QueryClient()` is created once per *process*. On
the server that process is shared by every visitor — so one user's
cached agent list could be served to another.

---

# Part D — The screens (4.3 – 4.8)

## D1. The dashboard, and why the empty state is the real screen

A new user has zero agents and zero services. Showing them an empty
table teaches nothing. So the empty state **is** the onboarding:

```text
┌──────────────────────────────────────────┐
│            No agents yet                 │
│                                          │
│  1. Connect a service   [Connect]        │
│  2. Create an agent     (locked)         │
│  3. Start chatting                       │
└──────────────────────────────────────────┘
```

Step 2 is disabled until step 1 is done. That ordering teaches the
product's model: an agent needs a connected service before it can do
anything.

Three separate queries, not one combined endpoint — they run in
parallel, each caches on its own key, and connecting a service
invalidates only the connections query.

## D2. Plugins — nothing about GitHub is written in the frontend

The catalogue comes from the live tool registry. The detail page groups
tools by what they do:

```text
READ (7)                        ● Safe
  Search files      find files by name
  Get file          file metadata

WRITE (4)                       ● Medium risk
  Create file       create a new file      Asks first

DELETE (1)                      ● Critical
  Delete file       permanently delete
```

**None of that grouping is hardcoded.** It comes from `operation`,
`risk_level` and `requires_approval`, which Phase 2 computed by
classifying every tool. `lib/tools.ts` only decides how those facts are
*worded and coloured*:

```ts
export const OPERATION_HELP: Record<Operation, string> = {
  read:   "Only looks at your data. Cannot change anything.",
  write:  "Creates or edits things in your account.",
  delete: "Removes things. Some deletions cannot be undone.",
  admin:  "Can change who is allowed to see your data.",
};
```

`admin` is a developer's word. "Can change who is allowed to see your
data" is what actually happens — and it is the sentence that makes
someone pause before ticking the box.

> **If you ever write `if (toolName.startsWith("github_"))` in a
> component, stop.** The classification already exists; you are
> re-implementing it in the wrong place.

Add a new service to the MCP server and it appears here — with its
tools, risk levels and approval flags — with no frontend change at all.

A group is badged with its **worst** tool, never an average. A group
containing one critical tool is a critical group; averaging would hide
exactly the thing the user needs to see.

## D3. The agent wizard — four steps, nothing lost

```text
[1 Identity] → [2 Instructions] → [3 Services] → [4 Tools]
```

The draft is saved to `sessionStorage` on every change, so a refresh
loses nothing. Two details:

**Why restore in an effect, not in `useState`?** `sessionStorage` does
not exist on the server. Reading it during the first render would crash
the server render, and returning different values on server and client
is a *hydration mismatch* — React builds one tree, finds another, and
complains.

**Why `sessionStorage` and not `localStorage`?** A half-finished agent
belongs to this tab. It should not reappear in a different tab three
weeks later.

The tool selection defaults to **every read tool**, mirroring Phase
3.6's rule: reads on, writes off. Safe by default; the user opts in to
anything that changes their accounts.

## D4. The tool picker — the screen that makes the product feel serious

Four rules, enforced rather than trusted:

```text
1. Reads default ON. Writes default OFF.
2. Anything with requires_approval shows a warning badge.
3. CRITICAL tools need a second confirmation to enable.
4. A live count is always visible: "12 of 61 tools enabled".
```

Rule 3 only applies in one direction:

```ts
if (next && safeRisk(tool.risk_level) === "critical") {
  setConfirming(tool);   // ask before turning ON
  return;
}
apply(tool.name, next);  // turning OFF never asks
```

Making it harder to become *safer* would be absurd.

In the confirmation dialog, **Cancel comes first in the DOM** so it
takes focus by default. The safe choice should never be the accidental
one.

The live count uses `aria-live="polite"`, so a screen-reader user is
told the number changed without having to go looking for it.

> **Why this matters commercially:** a client handing an AI access to
> their GitHub account wants to see exactly what it can do. This screen
> is the difference between "clever demo" and "software I trust".

### Saving is a PUT, and that changes the payload

```ts
// EVERY tool is sent, enabled true or false — not just the ticked ones.
const payload = Object.fromEntries(
  (agent.data?.tools ?? []).map(tool => [
    tool.tool_name,
    { enabled: selected.has(tool.tool_name) },
  ]),
);
```

The endpoint is a PUT: the body **is** the complete new state. A
partial update has no way to express "this box was unchecked", because
an absent key is indistinguishable from a key the client never knew
about.

## D5. Chat

The header shows the agent, its services and its tool count. The body
scrolls; the input box does not.

```tsx
<div className="flex h-[calc(100svh-8rem)] flex-col">
  <header ... />
  <div className="min-h-0 flex-1 overflow-y-auto"> ... </div>
  <MessageInput ... />
</div>
```

`min-h-0` is not decoration. A flex child refuses to shrink below its
content by default, so without it the whole page grows and the input
box scrolls off the bottom of the screen.

Two input details:

```ts
if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
  e.preventDefault();
  submit();
}
```

`shiftKey` is what separates "send" from "write a second paragraph".
`isComposing` matters for anyone typing Hindi, Japanese or Chinese
through a candidate window — there, Enter **confirms the character**,
and sending on that keystroke would fire the message mid-word every
single time.

History and the live turn are kept separate:

```text
history   what the server has stored     (the truth)
live      the turn happening right now   (temporary)
```

When the turn finishes, history is reloaded from the server and `live`
is thrown away. That is what makes refreshing mid-conversation safe:
there is never a phantom message that exists only in the browser.

`role='tool'` messages are stored for the audit trail but hidden from
the transcript. They are large and mostly noise; the assistant's own
summary already says what happened, and the timeline shows the
mechanics.

## D5b. The conversation list

Before this existed, "New chat" was a **one-way door**. It opened a
fresh conversation and there was no way back — the old one was still in
the database, but the UI had no door to it.

```text
┌──────────────┬──────────────────────────────┐
│ + New chat   │  ← Agents                    │
│              │  Developer Agent   [Settings]│
│ Remember the ├──────────────────────────────┤
│ word apricot │                              │
│ 2m ago    ⋯  │  You                         │
│              │  Remember the word apricot   │
│ List my open │                              │
│ issues       │  Agent                       │
│ 1h ago    ⋯  │  Noted.                      │
└──────────────┴──────────────────────────────┘
```

Chat names are not typed by the user. The backend derives them from the
first message (`derive_title` in `conversation_service.py`) — no model
call, because asking an LLM to summarise would add cost and latency to
every new chat, while the person is already waiting for their first
answer.

### The open chat lives in the URL

```text
/agents/{agentId}/chat?c={conversationId}
```

Keeping it in React state instead would mean three separate annoyances:

```text
refresh        drops you back into the newest chat
Back button    leaves the agent entirely, instead of returning to
               the chat you were just reading
sharing        impossible - there is no address for "this chat"
```

The URL is the one piece of state the browser already knows how to
save, restore and share.

> **The general rule:** anything a person would expect the Back button
> to undo belongs in the URL, not in `useState`.

`router.replace` is used rather than `push`, so flicking through five
chats does not leave five entries in the history stack.

### One detail that is easy to miss

```tsx
<ChatSurface key={conversationId} ... />
```

The `key` forces React to **rebuild** the chat when you switch
conversations. Without it React reuses the component, `useChat` keeps
the previous transcript while the new one loads, and you see half a
second of the wrong conversation. A skeleton is better than wrong
content.

### Small accessibility details worth copying

```tsx
aria-current={isActive ? "page" : undefined}
```

Colour alone does not say "this is the one you are reading".

```tsx
className="opacity-0 focus:opacity-100 group-hover:opacity-100"
```

The `⋯` menu is hidden until hover — but `focus:opacity-100` is what
keeps it reachable by keyboard, because a keyboard user never hovers.
Hover-only controls are one of the most common ways a UI quietly
excludes people.

---

## D6. The tool timeline

Collapsed by default, expandable per row:

```text
┌─ Tools  2 of 2 ─────────────────── 1.4s ──┐
│ ✓ Search pull requests   Worked    312ms  │
│ ⊘ Create issue           Not allowed  0ms │
└───────────────────────────────────────────┘
```

Three statuses, three meanings, kept visually distinct:

```text
✓ success   it worked
✕ failed    something went wrong
⊘ denied    the system correctly refused
```

**`denied` is not an error.** Merging it into `failed` would make your
own dashboard report broken software every time your safety rules did
their job. That is the same reason `ExecutionStatus` separates them in
Phase 2.

Every row shows the status **word**, not only a colour and a symbol.
Roughly one man in twelve cannot reliably separate red from green.

When a tool fails, the expanded row shows the recovery hint that the
executor already attached:

```text
▼ Get file            Failed   88ms

  The requested resource does not exist. Check identifiers for
  typos, or search for the correct one first.
```

That text comes from `RECOVERY_HINTS` in `agent/errors.py` — written
once in Phase 2, displayed for free here.

## D7. The approval notice — and an honest gap

Phase 4.7 asks for a dialog with **Approve** and **Cancel**, where
Approve lets the tool run.

**We cannot build that yet, and pretending otherwise would be the worst
possible outcome.**

Today `api/approvals.py` *denies* any call needing confirmation. It
records the refusal as `denied` (not `failed`), and reports it in
`approvals_required`. That was a deliberate Phase 3 decision, and the
reasoning in that file is worth reading: auto-approving would be one
line and the wrong line, because the approval flag would still *look*
enabled in the UI while doing nothing.

Making Approve actually execute needs a **suspendable turn** — persist
the pending call, return, and re-enter the loop when the user clicks.
That is Phase 5.

So the component tells the truth:

```text
┌────────────────────────────────────────────┐
│ Your agent needed permission for 1 action  │
│ Nothing was changed in your accounts.      │
│                                            │
│ ⊘ Create issue   ● Medium risk  [Details]  │
└────────────────────────────────────────────┘
```

Verified end to end against the live stack:

```json
"approvals_required": [
  { "tool_name": "github_create_issue",
    "operation": "write",
    "risk_level": "medium",
    "argument_keys": ["owner", "repo", "title", "body"] }
]
"timeline": [["github_create_issue", "denied"]]
```

No issue was created. The gate holds.

### Two honest limits, written into the UI

**Arguments.** Phase 4.7 rightly insists on showing the *actual*
arguments — "Approve this action?" with no detail trains people to
click Approve blindly. The backend currently sends `argument_keys`
only: the names, not the values. So the names are what is shown, and
the gap is stated plainly.

**A bug this exposed.** My first version read the approvals from the
live turn only. When the turn finished, `setLive(null)` ran and the
notice **flashed and vanished**. The fix was to derive it from stored
data as well:

```ts
export function refusedFromExecutions(executions) {
  return (executions ?? [])
    .filter(e => e.status === "denied")
    .map(e => ({ tool_name: e.tool_name, operation: e.operation, ... }));
}
```

Now it comes from the database, so it also survives a reload — which is
what Phase 4's milestone 5 requires.

## D8. The activity page

```text
Activity                          [All ▾] [7 days ▾]

Total runs   Worked   Failed   Not allowed
    142        138       3          1

Today
  ✓ List issues      Developer Agent    421ms
  ✕ Get file         Research Agent      88ms
  ⊘ Delete repo      Developer Agent       0ms
```

The filters are part of the query key, and the page uses
`useInfiniteQuery`:

```ts
useInfiniteQuery({
  queryKey: ["executions", filters],        // the cursor is NOT here
  queryFn: ({ pageParam }) => listExecutions({ ...filters, cursor: pageParam }),
  getNextPageParam: (last) => last.has_more ? last.next_cursor : undefined,
  placeholderData: keepPreviousData,
});
```

Change a filter and TanStack treats it as a different query, so caching
is correct for free instead of a manual refetch racing the old one.

The **cursor is deliberately not in the key**. It is page state
belonging to this query; putting it in the key would make every page a
separate cache entry that forgets the ones before it.

The rows are *derived* from the cache on every render:

```ts
const rows = executions.data?.pages.flatMap(page => page.items) ?? [];
```

Never copied into `useState`. The first version of this page did copy
them, and it produced bug 10 in section 12 — worth reading, because the
mistake is easy to repeat.

---

# Part E — Streaming (4.9)

A tool-using turn takes 5–30 seconds. A silent spinner feels broken.
Server-Sent Events (SSE) let the server push progress as it happens.

The events your backend emits:

```text
routing        which services were chosen
round_start    a new thinking round
tool_start     a tool is about to run
tool_end       it finished (this one has the full record)
escalation     the agent is trying a different approach
answer_ready   the model has finished writing
done           the turn is saved  (message id, full response)
error          the turn failed
```

> There is **no `token` event**. Phase 4.9 mentions one, but your
> backend sends the whole answer in `answer_ready`. So text appears in
> one step rather than word by word. Not a bug — a known gap.

## E1. Why not `EventSource`

`EventSource` is the browser's built-in way to read SSE. It cannot be
used here, for two independent reasons:

```text
1. It only ever issues GET.
   Our endpoint is POST — the message is the request body.

2. It cannot set headers.
   So no `Authorization: Bearer ...`.
```

The usual workaround for (2) is putting the token in the query string.
That token then lands in server logs, proxy logs and browser history.
Not acceptable for a credential.

So `lib/hooks/use-sse.ts` uses `fetch` and parses the SSE framing by
hand. It is about forty lines.

**What we give up:** `EventSource` reconnects automatically; `fetch`
does not. That is fine, and arguably better — an agent turn is *not
idempotent*. It may have already created a GitHub issue. Silently
re-sending the message on a dropped connection could do the work twice.
A dropped turn is surfaced to the user, who decides.

## E2. Parsing the stream

```ts
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });

  const normalised = buffer.replace(/\r\n/g, "\n");
  const frames = normalised.split("\n\n");

  buffer = frames.pop() ?? "";     // keep the incomplete tail
  for (const frame of frames) { ... }
}
```

Three details that are easy to get wrong:

**`{ stream: true }`** — a chunk boundary can fall in the middle of a
multi-byte character. Without this flag, one emoji split across two
chunks decodes as two broken characters.

**Normalising `\r\n`** — some proxies rewrite line endings. Splitting
on `"\n\n"` alone would then never match and the UI would receive
nothing at all.

**Keeping the tail in `buffer`** — the last piece after a split is
either an incomplete frame or an empty string. Parsing it would produce
broken JSON.

## E3. The bug that took longest: SSE buffering

This is the most instructive bug in Phase 4.

**Symptom.** The tool timeline never appeared while the agent worked.
Everything arrived at once when the turn ended.

**Measurement first.** Rather than guess, I timed the same request two
ways:

```text
direct to FastAPI :8077
  routing       +0.02s
  tool_start    +2.74s
  tool_end      +3.37s
  answer_ready  +12.52s

through Next :3100
  EVERYTHING at +17.24s
```

So the API was fine. Something in Next was holding the events.

**The wrong guesses, and how each was eliminated.** This is the part
worth copying as a method:

```text
Guess 1: my route handler code buffers.
  Test:  pump the stream explicitly instead of passing the body.
  Result: still buffered. Not it.

Guess 2: the `Connection: keep-alive` header I set.
  (It IS wrong — a hop-by-hop header a Response must not set.)
  Test:  remove it.
  Result: still buffered. Not the cause, but correctly removed.

Guess 3: Next cannot stream at all.
  Test:  a tiny route that emits SSE itself, no upstream.
  Result: ticked once per second, perfectly. Next streams fine.

Guess 4: Node's fetch buffers the upstream response.
  Test:  a plain Node script fetching :8077 directly.
  Result: streamed perfectly. Not it.
```

Next streams. Node's fetch streams. But together they did not. Which
meant my route handler **was never running at all**.

**The cause.** From the Next.js documentation: when `rewrites()`
returns an *array*, those rewrites are applied after filesystem routes
but **before dynamic routes**. My streaming handler lives at:

```text
app/api/conversations/[id]/messages/stream/route.ts
                        ^^^^  a DYNAMIC route
```

So the `/api/:path*` rewrite swallowed it every time, and the buffering
proxy handled the stream instead.

**The false clue.** I checked for my own `X-Accel-Buffering: no` header
and saw it, which convinced me the handler was running. It was not
mine — `sse-starlette` sets that header itself, and the rewrite was
passing it through.

> **The lesson:** a header you did not uniquely author is not proof of
> which code ran. If I had used a header with a nonsense name I chose,
> I would have found this in five minutes instead of an hour.

**The fix** — the `fallback` bucket, which runs *after* every Next
route has had its chance:

```ts
async rewrites() {
  return {
    fallback: [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
    ],
  };
}
```

**Result:**

```text
through Next, after the fix
  routing  +0.14s      ← was +17.24s
```

The route handler also sets headers that matter once this sits behind a
real proxy:

```ts
"Cache-Control": "no-cache, no-transform",
"X-Accel-Buffering": "no",     // nginx honours this
```

## E4. Making events idempotent

Phase 4.9 says to key everything by `execution_id`. There is a wrinkle:

```text
tool_start  →  { tool, round }              ← no execution_id!
tool_end    →  the full ExecutionRecord     ← has one
```

`tool_start` has no id because the executor has not created the record
yet. So a pending row gets a synthetic key, and `tool_end` resolves the
**oldest still-pending row with the same tool name**:

```ts
const index = prev.timeline.findIndex(
  row => row.pending && row.tool_name === execution.tool_name,
);
```

This is correct because the loop `await`s each `execute()` — tools run
one at a time, so first-pending is always the one that just finished,
even when the same tool is called twice in a round.

Duplicate events are handled by checking first:

```ts
if (prev.timeline.some(row => row.execution?.id === execution.id)) {
  return prev;     // already resolved; a replay changes nothing
}
```

And an unknown event name is **ignored**, not thrown on. A newer
backend adding an event should not break the whole turn.

---

# Part F — The backend piece that was missing

Phase 4.8 needs an executions page. Phase 3 never built the endpoint.
So Phase 4 added it.

```text
api/schemas/execution.py      ExecutionListItem, ExecutionPage, ExecutionStats
api/services/execution_service.py   filters, aggregate counts
api/routers/executions.py     GET /api/executions, GET /api/executions/stats
api/pagination.py             + StringCursor
```

## F1. Why a separate schema from `ExecutionRead`

Context. Inside a conversation the reader already knows which agent is
talking, so the timeline does not repeat it. The activity page spans
every agent, so the agent name is the first thing a row must say.

Returning `ExecutionRead` here and "just adding a field later" is how
one schema ends up serving two screens badly.

## F2. `StringCursor` — why the existing cursor did not fit

`Cursor` stores `(created_at, row_id: uuid.UUID)`. But:

```text
tool_executions.id = "exec_0ecb4fe1c044"     ← a STRING, not a UUID
```

Reusing `Cursor` would mean parsing that as a UUID and raising
`InvalidCursor` on every single page request. So `StringCursor` is the
same idea with a text id, and `build_page` gained a `cursor_cls`
parameter.

### Why cursors at all — the thing that must not break

Both cursors compare a **pair**, using a row-value comparison:

```python
stmt = stmt.where(
    tuple_(ToolExecution.started_at, ToolExecution.id)
    < (position.created_at, position.row_id)
)
```

Not two ANDed conditions. If ten executions share a timestamp to the
microsecond — which happens constantly, because a turn writes several
at once — then `started_at < :t AND id < :i` silently drops every row
that ties on the timestamp but sorts after on id.

**This was tested properly, not assumed.** I inserted 25 rows all
sharing one identical `started_at` and paged through them 7 at a time:

```text
pages walked : 4
rows returned: 25
unique rows  : 25
no duplicates: True
all 25 found : True
```

## F3. Aggregates belong in the database

```python
base = (
    select(ToolExecution.status, func.count(), func.avg(ToolExecution.duration_ms))
    .where(...)
    .group_by(ToolExecution.status)
)
```

A user with 50,000 executions should cost one aggregate query, not
50,000 objects loaded into Python memory.

One subtlety — the average is **weighted**:

```python
total_ms += (avg_ms or 0.0) * count
```

Averaging the per-status averages would weight a single denied call as
heavily as 400 successes.

## F4. Small things that are easy to get wrong

```python
status_filter: str | None = Query(default=None, alias="status")
```

The parameter is aliased because `status` is also the FastAPI module
imported for status codes. The query string keeps the name a user would
expect; the Python name avoids shadowing.

```python
limit: int = Query(default=50, ge=1, le=200)
```

`le=200` is a guard, not a preference. Without an upper bound a client
can ask for `?limit=1000000` and turn a page request into a full table
scan holding a database connection for minutes.

```python
.outerjoin(Agent, Agent.id == ToolExecution.agent_id)
```

An **outer** join: `agent_id` is nullable, and an execution whose agent
was deleted must still appear. An inner join would silently drop those
rows and the page would quietly lie about how much activity there was.

All of it verified:

```text
empty list      200  {items: [], has_more: false}
bad status      422  "status must be one of ['denied','failed','success']"
bad cursor      422  "Invalid cursor."
limit 9999      422
25 tied rows    paged correctly, no duplicates, none missing
```

---

# Part G — Testing (4.10)

## G1. What is tested, and against what

24 end-to-end tests driving a real Chromium browser against the **real
backend** — real PostgreSQL, real MCP server, and for the chat tests a
real model.

```text
auth.spec.ts        6   register, login, logout, reload, validation
plugins.spec.ts     3   catalogue, connect/disconnect, grouping
agents.spec.ts      4   wizard, safe defaults, edit, draft survival
chat.spec.ts        2   send + timeline, reload mid-conversation
approval.spec.ts    1   a write tool is refused and explained
executions.spec.ts  3   empty state, filters, filter caching
failure.spec.ts     2   dropped stream, 503 from the tool server
rendering.spec.ts   3   message order, Markdown, verbatim user text
conversations.spec.ts 3 old chats reachable, URL state, rename/delete
```

Phase 4.10 suggests **mocking** the backend so a failing test means the
UI broke rather than the network. That is right for a large suite. But
these tests exist to prove the whole product works together, and a mock
cannot prove that. A mocked chat test would have passed happily while
the SSE buffering bug made the feature useless.

## G2. The change that made the tests trustworthy

At first the tests were run against `next dev`. Results moved around
between runs — six failures, then three, then a different three.

The cause was not flaky code. In development, Next compiles each route
**the first time it is requested**:

```text
first visit to /dashboard   18 seconds
later visits                200ms
```

So a test passed or failed depending on which earlier test happened to
warm that route.

The fix is in `playwright.config.ts` — build once, then serve:

```ts
webServer: {
  // NEXT_DIST_DIR keeps this build out of `.next`, so running the
  // tests can never break a dev server someone has open. See G4.
  command:
    "NEXT_DIST_DIR=.next-e2e npm run build && " +
    "NEXT_DIST_DIR=.next-e2e npm run start -- --port 3100",
  url: "http://127.0.0.1:3100/login",
  reuseExistingServer: !process.env.CI,
},
```

The same tests then ran in **under a second each**, and stayed stable.

> **The lesson:** if tests fail in different places each run, suspect
> the environment before the code.

Two more settings, both deliberate:

```ts
fullyParallel: false, workers: 1,
// Every test registers a real user against one shared database.

retries: 0,
// A failing assertion should never be retried into a pass. A flaky
// test that "passes on retry" is a bug report you threw away.
```

## G3. Five test-writing mistakes I made

These are worth knowing because they look like application bugs.

**`getByText` matches a substring, case-insensitively, by default.**

```ts
page.getByText("Tools")        // ALSO matches "18 tools" → 2 elements
page.getByText("Tools", { exact: true })
```

**`getByRole("alert")` was ambiguous** — the toast container also
carries `role="alert"`. Matching by text was the right fix.

**My own markup made a label unfindable:**

```tsx
// WRONG — the element's text is "Tools1 of 1"
<span>Tools<span>{finished} of {rows.length}</span></span>

// RIGHT — two siblings
<span><span>Tools</span><span>{finished} of {rows.length}</span></span>
```

The test failure was real information: nothing on the page had the
accessible text "Tools", which is bad for screen-reader users too.

**`getByRole` name matching is a substring too.**

```ts
page.getByRole("option", { name: "All" })   // ALSO matches "Not allowed"
page.getByRole("option", { name: "All", exact: true })
```

**The one I made three times: matching text that appears twice.**

A chat's title is derived from its first message, so the same words
legitimately appear in two places at once:

```text
sidebar     "Remember the word zebra"     ← the chat's name
transcript  "Remember the word zebra"     ← the message itself
```

An unscoped `getByText` matches both, Playwright refuses in strict
mode, and the failure reads like a bug in the app. It is not — it is
the feature working. The fix is to say *where* you are looking:

```ts
const transcript = page.getByTestId("user-message");
await expect(transcript.filter({ hasText: "..." })).toBeVisible();
```

Which is also why the message bubbles carry `data-testid`. Asserting on
CSS classes would couple the tests to styling, so a colour change would
break tests that have nothing to do with colour.

This one has a sting in the tail worth knowing about. Adding the
conversation sidebar **broke a test that had been passing** — the
ordering test asserted on `getByText("Say hello")`, which was unique
until the sidebar started showing that same text as a chat title.

```text
before the sidebar   "Say hello" → 1 element  → test passes
after the sidebar    "Say hello" → 2 elements → strict mode violation
```

Nothing about message ordering changed. A loosely-written assertion
became wrong because the page around it grew.

> **The lesson underneath all five:** when a test fails, ask whether the
> *assertion* is wrong before assuming the *app* is. Four of these five
> looked exactly like product bugs and were not. The habit that prevents
> them is to say **where** you are looking — `page.getByTestId(...)` or
> `page.locator("main ...")` — instead of searching the whole document
> and hoping the text stays unique.

And one that was *not* my test's fault — it found a real bug. Typing
into the chat immediately after clicking "+ New chat" left the Send
button permanently disabled. The test was racing an async navigation,
but so would a real person. See bug 12 in section 12.

## G4. A trap that cost twenty minutes — now designed out

Running `npm run build` while `next dev` was running **broke the dev
server**. Both write to the same `.next/` directory, so the production
build overwrote the dev server's chunks and every page 404'd its
JavaScript.

```text
Symptom: pages render blank, console shows 404 for
         /_next/static/chunks/app/(app)/layout.js
```

The first response was a note in this document saying "do not do that".
That is the weakest kind of fix — it relies on remembering. It then bit
me a second time while writing the tests, which is exactly what a
remember-to rule earns you.

So the collision was made **impossible** instead:

```ts
// next.config.ts
distDir: process.env.NEXT_DIST_DIR ?? ".next",
```

The test suite sets `NEXT_DIST_DIR=.next-e2e`, so it builds into its own
folder and cannot touch a running dev server.

One follow-on: ESLint then walked the new folder and reported 7,267
"problems" in generated code. The ignore list needed a glob, not a
literal:

```js
ignores: [".next*/**", "playwright-report/**", "test-results/**"]
```

> **The general lesson:** when a rule exists only in someone's memory,
> it is not a fix. Change the system so the mistake cannot be made.

---

## 11. Following one message end to end

You type "List my open GitHub issues" and press Enter.

```text
 1  MessageInput          Enter without Shift, not composing → submit()

 2  useChat.send()        live = { phase: "routing", question, timeline: [] }
                          The question appears instantly. No waiting.

 3  use-sse.streamTurn()  POST /api/conversations/{id}/messages/stream
                          Authorization: Bearer <token from memory>

 4  route.ts              The Next route handler (NOT the rewrite —
                          that is what `fallback` bought us) forwards
                          to FastAPI and pipes the stream back.

 5  FastAPI               Phase 3: route → filter by the agent's
                          enabled tools → run_agent → persist.

 6  event: routing        phase → "thinking"
                          "Working out which tools to use..."

 7  event: tool_start     a pending row appears
                          "Running List issues..."

 8  event: tool_end       the row resolves to ✓ Worked  421ms
                          keyed by execution_id, so a replay is safe

 9  event: answer_ready   the answer text appears

10  event: done           approvals_required captured

11  useChat               loadHistory()  → the server is now the truth
                          setLive(null)  → the temporary turn is gone

12  invalidateQueries     the activity page and its counts are stale
```

If you refresh the page at step 12, everything is still there — because
step 11 replaced the browser's temporary copy with what the database
actually holds.

---

## 12. Every bug we hit, and what it taught

### Phase 3 bugs found while verifying it

**1. `DATABASE_URL` with an `@` in the password**

```text
postgresql+asyncpg://postgres:Postgres@1011@localhost:5432/Agent_Hub_MCP
                                      ^ splits here
```

The URL parser split on the *first* `@`, read the host as
`1011@localhost`, and failed with a DNS error that looked like a
network problem. `@` must be written `%40`.

**Lesson:** a connection string passes through several parsers, each
with its own special characters. Escaping is never "done" — it is done
*for a specific destination*.

**2. `%` in an Alembic URL**

Percent-encoding it exposed the next layer: Alembic reads `alembic.ini`
with `configparser`, where `%` starts a variable. Fixed by doubling it:

```python
get_settings().database_url.replace("%", "%%")
```

**3. Every tool reached the model with an empty schema** ← the big one

```python
data = tool.model_dump()          # key is "input_schema"
data.get("inputSchema", {...})    # looks for "inputSchema" → always the default
```

The MCP *wire format* uses camelCase `inputSchema`. The Python SDK's
*field* is `input_schema`, with camelCase only as an alias — and
`model_dump()` returns field names, not aliases.

So all 61 tools were described to the model as taking **no arguments**.
It never raised, never failed a test, and returned HTTP 200. The model
diagnosed it itself:

> "The tool has an empty schema, but calling it returns a validation
> error saying `owner` and `repo` are required."

The proof is in the database:

```text
github_search_repositories  success  404.7ms  {'query': '...'}
github_list_issues          failed     0.5ms  {}   ← before the fix
```

Empty arguments and 0.5ms means the call never left the machine.

**Lesson:** `dict.get(key, default)` cannot tell "missing because the
name is wrong" from "missing because there is no value". A wrong tool
schema does not crash — it produces an agent that is merely *bad*,
which is much harder to diagnose than one that is broken.

Note that `agent/discovery.py` already handled both spellings. The
knowledge existed in the codebase; it just was not applied on the
second path.

### Phase 4 bugs

**4. Could not reach `/register`** — a failed first-load refresh was
treated as "your session ended". See [B4](#b4-two-real-bugs-in-this-area).

**5. Sign out did nothing** — a 422 from a missing request body threw
before the redirect. See [B4](#b4-two-real-bugs-in-this-area).

**6. SSE arrived all at once** — array-form `rewrites()` run before
dynamic routes and swallowed the streaming handler. See
[E3](#e3-the-bug-that-took-longest-sse-buffering).

**7. The approval notice flashed and vanished** — it was read from the
live turn, which is cleared when the turn ends. Now derived from stored
`denied` executions as well, so it survives a reload.

**8. `next build` broke `next dev`** — both write to `.next/`. Now
impossible: the E2E build uses `distDir: .next-e2e` via `NEXT_DIST_DIR`.

**9. The answer appeared above the question** (reported by a user)

PostgreSQL's `now()` is the **transaction start time**, not the wall
clock, and one turn writes the user's message and the reply in a single
transaction. So both rows get a byte-identical `created_at`:

```text
user       2026-08-20T08:37:53.873322+00:00  "give me the auth data"
assistant  2026-08-20T08:37:53.873322+00:00  "Here are the details..."
```

With the timestamps tied, the only tiebreak left was the id — a random
UUID. The answer had a coin-flip chance of rendering above its own
question.

The display fix sorts ties by role, because within one turn the true
order is known and fixed: user → tool → assistant. See
`byConversationOrder` in `lib/hooks/use-chat.ts`.

**The proper fix is in the backend** and is not applied: use
`clock_timestamp()` instead of `func.now()`, or add an explicit
ordering column. `clock_timestamp()` advances during a transaction;
`now()` does not.

**10. Filters showed an empty list and made no request** (reported by
a user)

Pick "Today", pick "7 days", pick "Today" again — the list went blank
and the Network tab showed nothing.

The page kept its rows in a `pages` state array and filled that array
from **inside `queryFn`**:

```ts
queryFn: async () => {
  const page = await listExecutions({ ...filters, cursor });
  setPages(...)          // ← a side effect, inside the fetcher
  return page;
}
```

Changing a filter cleared `pages`. But the key for "Today" was already
cached and still fresh, so TanStack served the cache and **never ran
`queryFn`** — so `setPages` never ran either. Empty screen, no request.

Rewritten with `useInfiniteQuery`, which keeps every page in the cache
under one key. The rows are now *derived* from the cache on each
render, never copied into state.

Two rules worth keeping:

```text
Never do side effects inside queryFn.
  It runs only on a cache MISS, so anything hidden inside it silently
  stops happening exactly when caching starts working.

Never copy server data into component state.
  Two sources of truth drift, and the copy is the one on screen.
```

`placeholderData: keepPreviousData` was added at the same time, so a
filter change keeps the previous rows visible while the new ones load
instead of blanking the screen.

**11. Bold text was shown as literal asterisks** (reported by a user)

Models write Markdown — that is how they express structure. Rendering
the answer as plain text showed the raw characters, and a table of
GitHub issues became a wall of pipes.

Fixed with `react-markdown` + `remark-gfm`. Raw HTML is deliberately
NOT enabled: answer text can contain anything a tool returned from
GitHub or Slack, and injecting that as HTML would be a cross-site
scripting hole with a stranger on the other end.

The user's own messages are still shown verbatim. Someone who types
`**test**` meant those asterisks.

**12. Text vanished if you typed right after "+ New chat"** (found by
a test)

The Send button went permanently dead. The cause:

```text
click "+ New chat"   →  returns immediately
POST /conversations  →  still in flight
you type             →  into the CURRENT input
URL gains ?c=<new>   →  <ChatSurface key={conversationId}> REMOUNTS
                     →  MessageInput is rebuilt, its value resets to ""
                     →  Send is disabled because the box is empty
```

The `key` that remounts on conversation change is deliberate — it stops
half a second of the previous transcript showing. But it also throws
away in-progress input.

The input is now locked while a chat is being created, with the
placeholder "Starting a new chat...".

> Worth noting how this was found: a Playwright test raced the
> navigation, which is precisely what an impatient person does. A test
> that is "too fast" is often just a user who types quickly.

### Caught by the type checker before running

```text
12. AgentToolsUpdate.tools is a MAP, not a list
13. Operation has four values (read/write/delete/admin), not six
14. message.executions and agent.tools can be undefined
```

### The pattern across all of them

Look at bugs 3, 5 and 6 together:

```text
3.  wrong key name        → silent default, agent quietly useless
5.  missing request body  → 422 thrown before the redirect ran
6.  wrong rewrite bucket  → handler never ran, no error anywhere
```

**None of them raised an error.** Every one produced a *plausible wrong
result*. That is the class of bug worth fearing, and the only reliable
defence is to check the real behaviour end to end — which is why the
verification in Part G was worth more than any amount of code reading.

---

## 13. Troubleshooting guide

### "The page is blank and the console shows 404 for chunks"

`next build` was run while `next dev` was running, writing over the dev
server's files.

```bash
pkill -f 'next dev'; rm -rf .next; npm run dev
```

The test suite can no longer cause this — it builds into `.next-e2e`.
You can still trigger it by running `npm run build` yourself in the same
folder as a live `npm run dev`.

### "I typed straight after clicking New chat and Send is dead"

Fixed: the box is locked and reads "Starting a new chat..." until the
new conversation is ready. If you see it again, check that `disabled`
is still threaded from `creating` down to `MessageInput`.

### "Old chats are missing"

They are in the sidebar on the left, or behind the **Chats (n)** button
on a narrow screen. If the sidebar is empty, check the
`["conversations", agentId]` query — `useChat` invalidates the
`["conversations"]` prefix after every turn.

### "Everything redirects me to /login"

Check whether `/api/auth/refresh` returns 200 or 401. If 401, the
refresh cookie is missing or expired — sign in again. If it returns 200
and you still bounce, check the `isLoading` guard in
`app/(app)/layout.tsx`.

### "Progress does not appear until the turn ends"

The streaming route is being bypassed. Check `next.config.ts` uses
`fallback:`, not a bare array. Confirm with a header only your handler
sets — never one the upstream might also send.

### "TypeScript complains about a field that exists"

Regenerate the types; the API changed.

```bash
npm run gen:api
```

### "A checkbox does nothing"

You mutated state instead of replacing it. Make a new `Set`/array.

### "An effect runs forever"

Its dependency is a new object or array each render. Depend on a
stable value (a joined string), and compare before setting state.

### "Tests fail in different places each run"

You are testing against `next dev`. Use the production build.

### "The agent says a tool has no parameters"

The `input_schema` / `inputSchema` bug is back. Check
`mcp_tool_to_ollama_tool` in `agent/loop.py` reads **both** spellings.

---

## 14. How to add things

### A new screen

1. Create `app/(app)/thing/page.tsx` — it is protected automatically by
   the group layout.
2. Add it to `NAV` in `app/(app)/layout.tsx`.
3. Fetch with `useQuery` and a new query key.

### A new API call

1. `npm run gen:api` — do this **first**.
2. Add a typed function to the right file in `lib/api/`.
3. Never call `fetch` directly from a component.

### A new service (GitHub, Slack, …)

Nothing to do in the frontend. Register it on the MCP server and it
appears in the catalogue, the tool picker and the timeline — with its
risk levels and approval flags — automatically.

### A new tool field

1. Add it in Python.
2. `npm run gen:api`.
3. TypeScript will point at every place that must change.

### A new E2E test

Use `registerUser(page)` from `helpers.ts` so each test gets a fresh
user. Prefer `getByRole` over CSS selectors, and remember `getByText`
matches substrings.

---

## 15. Known gaps

Things deliberately not done, and where they belong.

```text
Approve-and-run              Phase 5. Needs a suspendable turn.
                             The UI says so plainly rather than
                             showing a button that lies.

Argument VALUES in the       The backend sends argument_keys only.
approval notice              Phase 4.7 wants values.

Word-by-word answers         The backend has no `token` event; it
                             sends the whole answer in answer_ready.

tests/api/                   Phase 3.11 was never built. The backend
                             still has no test coverage above the
                             engine layer. Given that verification
                             found two blocking bugs, this is the
                             most valuable thing left undone.

OAuth                        Phase 5. Today you paste a token.

Multi-tenant credentials     Phase 5. The MCP server still uses ONE
                             GitHub token for everyone — every user
                             of Agent Hub currently shares yours.

Mobile layout                Responsive basics are in place: the app
                             nav hides under `md`, and the chat list
                             becomes a "Chats (n)" toggle. Not tested
                             on real devices.

npm audit                    3 high-severity findings, all libvips
                             CVEs inherited via `sharp` (Next's image
                             optimiser). Only fixable with a breaking
                             major bump.
```

---

## 16. Glossary

**App Router** — the Next.js routing system where folders under `app/`
become URLs.

**Client component** — a component sent to the browser. Marked
`"use client"`. Can hold state and handle clicks.

**CORS** — the browser rule that stops a page on one origin from
calling another. Avoided here by proxying.

**Cursor pagination** — "give me what comes after this row", instead of
"skip the first 40". Does not break when rows are inserted mid-scroll.

**Hydration** — React taking server-rendered HTML and attaching
behaviour to it in the browser. A *hydration mismatch* is when the two
do not agree.

**httpOnly cookie** — a cookie JavaScript cannot read. The browser
still sends it.

**Idempotent** — doing it twice has the same effect as doing it once.
Agent turns are **not** idempotent, which is why they are never
auto-retried.

**Origin** — scheme + host + port. `localhost:3000` and
`localhost:8077` are different origins.

**Query key** — the array TanStack Query uses to identify a cached
request, e.g. `["agents"]`.

**Rewrite** — a Next rule that forwards a URL somewhere else on the
server side. The browser never sees it.

**Route group** — a folder in `(parentheses)`. Groups routes without
adding to the URL.

**Route handler** — a `route.ts` file. Server-side code that answers a
request directly, instead of rendering a page.

**Server component** — the default. Runs on the server, sends HTML, its
code never reaches the browser.

**SSE (Server-Sent Events)** — a one-way stream from server to browser
over plain HTTP. Events are separated by a blank line.

**Zod** — a library for checking data shapes *while the program runs*.
TypeScript only checks before it runs.

---

## The five ideas worth carrying to your next project

1. **Generate what two systems must agree on.** The API description
   already exists. Two hand-written copies of one shape always drift,
   and the drift is silent. This caught two of my own wrong assumptions
   before a single request was sent.

2. **Measure before you guess.** The streaming bug survived three wrong
   theories. Timing the same request two ways took two minutes and
   pointed at the right layer immediately.

3. **A plausible wrong answer is worse than a crash.** The empty tool
   schema returned HTTP 200 and a well-written reply. Test the real
   behaviour end to end, or you will ship it.

4. **Tell the truth in the interface.** A button that says "Approve"
   but cannot approve is worse than no button. The approval notice
   explains exactly what happened and what is not possible yet.

5. **Make the safe thing the default and the dangerous thing
   deliberate.** Reads on, writes off. Critical tools ask twice. Cancel
   takes focus. None of it is clever — all of it is the reason someone
   would trust this with their real accounts.

---

## Related documents

| Document | What it covers |
|---|---|
| `devloper_docs/Understand_Phse2.md` | Agent Engine — routing and execution |
| `devloper_docs/Understand_Phase3.md` | The backend — API, database, auth |
| `phases_doc/Phase4.md` | The Phase 4 specification |
| `phases_doc/Phase5.md` | Permissions, OAuth, multi-tenancy — next |
