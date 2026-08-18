# Phase 4 — Frontend

> **Status:** not started
> **Depends on:** Phase 3 (backend API)
> **Goal:** the interface that hides MCP entirely from the user.

Phase 4 is where Agent Hub becomes something a non-technical person
can use. The measure of success is simple:

> A user should think **"connect GitHub → create an agent → ask it about
> my repository"** and never once encounter the words *MCP*, *tool
> schema* or *namespace*.

---

## Target architecture

```text
                        Next.js (App Router)
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   Server Components      Client Components         API Client
   (data fetching)        (interactivity)           (typed fetch)
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                ▼
                          FastAPI backend
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                REST (CRUD)             SSE (live turn)
```

---

# Phase 4.1 — Setup

```text
Next.js 14+   App Router
TypeScript    strict mode on
Tailwind CSS
shadcn/ui     accessible primitives you own the code for
TanStack Query  server state
Zod           runtime validation at the boundary
```

```text
frontend/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── register/page.tsx
│   ├── (app)/
│   │   ├── layout.tsx              sidebar + shell
│   │   ├── dashboard/page.tsx
│   │   ├── plugins/
│   │   │   ├── page.tsx
│   │   │   └── [key]/page.tsx
│   │   ├── agents/
│   │   │   ├── page.tsx
│   │   │   ├── new/page.tsx
│   │   │   └── [id]/
│   │   │       ├── page.tsx
│   │   │       ├── settings/page.tsx
│   │   │       └── chat/page.tsx
│   │   ├── executions/page.tsx
│   │   └── settings/page.tsx
│   └── layout.tsx
│
├── components/
│   ├── ui/                        shadcn primitives
│   ├── chat/
│   │   ├── message-list.tsx
│   │   ├── message-input.tsx
│   │   ├── tool-timeline.tsx       ⭐ the signature component
│   │   └── approval-dialog.tsx     ⭐
│   ├── agents/
│   │   ├── agent-card.tsx
│   │   └── tool-picker.tsx         ⭐
│   └── plugins/plugin-card.tsx
│
├── lib/
│   ├── api/                       typed client per resource
│   ├── hooks/
│   │   ├── use-chat.ts
│   │   └── use-sse.ts
│   └── types.ts                   mirrors the backend schemas
└── tests/e2e/                     Playwright
```

> **Keep `lib/types.ts` generated, not hand-written.** FastAPI emits an
> OpenAPI schema; `openapi-typescript` turns it into TypeScript. Two
> hand-maintained copies of the same shape always drift.

---

# Phase 4.2 — Auth in the browser

```text
LOGIN
  POST /api/auth/login
    → refresh token lands in an httpOnly cookie (set by the server)
    → access token returned in the body, kept IN MEMORY only

REQUESTS
  Authorization: Bearer <access token from memory>

ON 401
  POST /api/auth/refresh  (cookie sent automatically)
    → new access token
    → retry the original request ONCE
    → if refresh fails, redirect to /login
```

### Never store the access token in localStorage

```text
localStorage   any injected script can read it. One XSS and the
               attacker has a token they can use from anywhere.

memory         dies on refresh (the refresh cookie fixes that) and is
               not readable cross-origin.

httpOnly       JavaScript cannot read it at all - which is exactly
  cookie       why the refresh token lives there.
```

Losing the access token on page reload is a feature, not a bug: the
refresh cookie silently restores the session.

---

# Phase 4.3 — Dashboard

```text
┌──────────────────────────────────────────────────┐
│ Agent Hub                              User ▼    │
├──────────────────────────────────────────────────┤
│  Welcome back                                    │
│                                                  │
│   Agents      Plugins      Runs (7d)   Failed    │
│      3           2            142         4      │
│                                                  │
│  Your agents                                     │
│  ┌────────────────────────────────────────────┐  │
│  │ Developer Agent                            │  │
│  │ ● GitHub   ● Drive                         │  │
│  │ Last used 2 hours ago                      │  │
│  │                              [Open]        │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  Recent activity                                 │
│    ✓ github_list_issues            421ms        │
│    ✗ google_drive_get_file         88ms         │
│                                                  │
│                       [+ Create Agent]           │
└──────────────────────────────────────────────────┘
```

Every number here comes from data Phase 2 and 3 already produce —
`tool_executions` gives runs, failures and durations for free.

**Empty states matter more than the populated ones.** A new user has
zero agents and zero plugins. That screen is your onboarding:

```text
┌──────────────────────────────────────────────────┐
│              No agents yet                       │
│                                                  │
│   1. Connect a service      [Connect GitHub]     │
│   2. Create an agent        (locked until 1)     │
│   3. Start chatting                              │
└──────────────────────────────────────────────────┘
```

---

# Phase 4.4 — Plugins

```text
┌─────────────────────┐  ┌─────────────────────┐
│ ⬤ GitHub            │  │ ⬤ Google Drive      │
│ 18 tools            │  │ 13 tools            │
│                     │  │                     │
│ ● Connected         │  │ ○ Not connected     │
│ parth@example.com   │  │                     │
│ [Manage] [Remove]   │  │ [Connect]           │
└─────────────────────┘  └─────────────────────┘
```

Plugin detail shows the tools, **grouped by what they do** — using the
classification from Phase 2.2:

```text
Google Drive · 13 tools

READ (7)                              ● safe
  search_files      find files by name
  get_file          file metadata
  read_file         file contents
  ...

WRITE (4)                             ● medium · approval
  create_file       create a new file
  upload_file       upload from disk
  ...

DELETE (1)                            ● critical · approval
  delete_file       permanently delete a file

ACCESS CONTROL (1)                    ● high · approval
  create_permission share a file with someone
```

None of that grouping is hardcoded in the frontend. It comes from
`operation`, `risk_level` and `requires_approval` on every tool.

---

# Phase 4.5 — Agent builder

Four steps, resumable, nothing lost on refresh.

```text
[1 Identity] → [2 Instructions] → [3 Plugins] → [4 Tools]
```

### Step 4 — the tool picker ⭐

This is the screen that makes the product feel serious.

```text
GitHub · 18 tools                    [Select all reads]

  READ                                    ● safe
  ☑ list_issues          List issues
  ☑ search_issues        Search issues
  ☑ get_repository       Repository info
  ☑ search_code          Search code

  WRITE                                   ● medium
  ☑ create_issue         Create an issue        ⚠ asks first
  ☐ update_issue         Edit an issue          ⚠ asks first
  ☐ create_pull_request  Open a PR              ⚠ asks first

  ⚠ These tools change your GitHub account.
    Your agent will ask before using them.
```

Rules worth enforcing in the UI:

```text
1. Reads default ON. Writes default OFF.
2. Anything with requires_approval shows the warning badge.
3. CRITICAL tools need a second confirm to enable.
4. Show a live count: "12 of 61 tools enabled".
```

> **Why this matters commercially:** a client handing an AI access to
> their GitHub account wants to see exactly what it can do. This screen
> is the difference between "clever demo" and "software I trust".

---

# Phase 4.6 — Chat ⭐

```text
┌──────────────────────────────────────────────────┐
│ ← Developer Agent      ● GitHub  ● Drive    ⚙   │
├──────────────────────────────────────────────────┤
│                                          You     │
│                    Analyze my latest auth PR.    │
│                                                  │
│ Agent                                            │
│ I'll look at the PR and related docs.            │
│                                                  │
│ ┌─ Tools ─────────────────────────── 1.4s ────┐  │
│ │ ✓ github_search_pull_requests        312ms  │  │
│ │ ✓ github_get_pull_request            287ms  │  │
│ │ ✓ google_drive_search_files          583ms  │  │
│ │ ⚠ google_drive_get_file    not_found  88ms  │  │
│ └─────────────────────────────────────────────┘  │
│                                                  │
│ The PR changes token expiry from 24h to 1h...    │
│                                                  │
├──────────────────────────────────────────────────┤
│ Ask your agent...                          [➤]  │
└──────────────────────────────────────────────────┘
```

### The tool timeline component

Collapsed by default, expandable per row:

```text
▼ github_get_pull_request                    287ms

  Status      success
  Service     github
  Operation   read
  Arguments   owner: argus
              repo: test
              number: 124
  Execution   exec_7417a3c40fa2
```

For failures, show the recovery hint that the executor already
attaches:

```text
▼ google_drive_get_file            not_found   88ms

  The requested resource does not exist. Check identifiers for
  typos, or search for the correct one first.
```

That text comes from `RECOVERY_HINTS` in `agent/errors.py` — written
once in Phase 2, displayed for free here.

### Live states

```text
● Thinking...                   waiting for the model
● Searching GitHub...           tool_start received
✓ Found 3 pull requests         tool_end received
● Writing response...           token events arriving
```

---

# Phase 4.7 — Approval dialog ⭐

```text
┌──────────────────────────────────────────────────┐
│  ⚠  Approval required                            │
│                                                  │
│  Your agent wants to create a GitHub issue.      │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │ Repository   argus/test                    │  │
│  │ Title        Auth token expires too early  │  │
│  │ Body         The current 1h expiry...      │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  Risk: medium · This changes your GitHub account │
│                                                  │
│              [Cancel]      [Approve]             │
└──────────────────────────────────────────────────┘
```

Requirements:

```text
1. Show the ACTUAL arguments. Never "Approve this action?" with no
   detail - that trains users to click Approve blindly.
2. Colour by risk_level. CRITICAL gets a distinct, alarming treatment.
3. Include a timeout (5 min) with a visible countdown.
4. Cancel is the default focus, not Approve.
5. "Always allow this tool for this agent" is a Phase 5 feature -
   design the space for it now.
```

---

# Phase 4.8 — Executions page

```text
Executions                    [All ▾] [Failed] [7 days ▾]

Today
  ✓ github_list_issues        Developer Agent      421ms
  ✓ drive_search_files        Research Agent       583ms
  ✗ drive_get_file            Research Agent        88ms   not_found
  ⊘ github_delete_repo        Developer Agent         0ms   denied

Yesterday
  ✓ slack_send_message        Ops Agent          1,204ms
```

Three statuses, three meanings — keep them visually distinct:

```text
✓ success
✗ failed     something went wrong
⊘ denied     the system correctly refused
```

`denied` is not an error. Merging it with `failed` would make your own
dashboard lie to you — the same reason `ExecutionStatus` separates them.

---

# Phase 4.9 — SSE client

```typescript
// lib/hooks/use-sse.ts
//
// Reconnection is not optional. Laptops sleep, wifi drops, and phones
// background tabs aggressively. EventSource retries automatically -
// your job is to make the RESULT idempotent, so a replayed event does
// not duplicate a message in the UI. Key everything by execution_id
// and message_id.
```

Event handling:

```text
routing            → show "Choosing tools..."
tool_start         → append a pending row to the timeline
tool_end           → resolve that row by execution_id
approval_required  → open the dialog
token              → append to the streaming answer
done               → finalise, invalidate the conversation query
error              → show an inline error, keep the history
```

---

# Phase 4.10 — Testing

```text
tests/e2e/
├── auth.spec.ts          register → login → logout
├── plugins.spec.ts       connect → visible → disconnect
├── agents.spec.ts        create → configure tools → edit
├── chat.spec.ts          send → timeline appears → answer renders
└── approval.spec.ts      write tool → dialog → approve → executes
```

### Mock the backend for E2E

Playwright can intercept network calls. Deterministic fixtures mean
your UI tests do not depend on GitHub being up, and a failing test
means the UI broke — not the network.

### Accessibility is not optional for a client project

```text
keyboard navigation through the whole chat flow
focus trapped inside the approval dialog
aria-live on the streaming answer
visible focus rings
contrast >= WCAG AA
```

---

# One thing I would NOT build yet

```text
❌ Dark/light theme switcher     ship one good theme first
❌ Mobile app                    responsive web is enough
❌ Real-time collaboration       nobody asked
❌ Custom charting               a table beats a bad chart
❌ i18n                          unless the client needs it now
❌ Agent avatars/upload          use initials or a generated shape
```

---

# Phase 4 milestone

### Test 1 — a stranger can onboard alone

Give someone the URL with no explanation. They should reach a working
agent without asking you a question.

```text
register → connect GitHub → create agent → send a message → get an answer
```

### Test 2 — the timeline is understandable

A non-technical user looks at the tool timeline and can say what the
agent did. If they cannot, the labels are wrong.

### Test 3 — approval actually gates

```text
enable github_create_issue
ask the agent to create an issue
→ dialog appears with the real title and body
→ Cancel → no issue is created
→ retry → Approve → issue is created
```

### Test 4 — failure is legible

Disconnect the network mid-turn. The UI must show a clear error and
keep the conversation, not a blank screen or an infinite spinner.

### Test 5 — reload is safe

Refresh mid-conversation. History is intact and the session survives.

**If all five pass, Phase 4 is genuinely done.**

---

## 🎯 Your next immediate task

```text
1.  Next.js + Tailwind + shadcn setup
        ↓
2.  Generated API types from OpenAPI
        ↓
3.  Auth pages + token refresh interceptor
        ↓
4.  App shell (sidebar, header, routing)
        ↓
5.  Plugins list + connect flow
        ↓
6.  Agent list + create wizard
        ↓
7.  Tool picker (grouped by operation, badged by risk)
        ↓
8.  Chat page (non-streaming first)
        ↓
9.  Tool timeline component
        ↓
10. SSE streaming
        ↓
11. Approval dialog
        ↓
12. Executions page
        ↓
13. Playwright E2E
```

> **Build step 8 before step 10.** A working non-streaming chat proves
> the whole stack end to end. Streaming is a refinement on top of
> something that already works.
