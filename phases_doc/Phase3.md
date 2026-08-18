# Phase 3 — Backend

> **Status:** not started
> **Depends on:** Phase 2 (complete — Agent Engine with routing + execution)
> **Goal:** turn the CLI agent into a multi-user HTTP service.

Yes — **you can move to Phase 3 now.** Phase 2 delivered a working
Agent Engine: dynamic discovery, 61 classified tools, a typo-tolerant
multi-service router, and a safe executor with permissions, approval,
retries and telemetry. All 147 tests pass.

But everything so far runs as **one process, for one person, in a
terminal**. Phase 3 is where that becomes a product.

---

## What actually changes

```text
              PHASE 2 (today)                  PHASE 3 (target)

            ai_client.py (CLI)              Next.js / any HTTP client
                   │                                  │
                   │                                  ▼
                   │                          ┌──────────────┐
                   │                          │   FastAPI    │
                   │                          │  auth, CRUD  │
                   │                          └──────┬───────┘
                   │                                 │
                   ▼                                 ▼
            ┌─────────────┐                   ┌─────────────┐
            │ AgentEngine │                   │ AgentEngine │  (shared)
            └──────┬──────┘                   └──────┬──────┘
                   │                                 │
                   ▼                                 ▼
            ┌─────────────┐                   ┌─────────────┐
            │ MCP Server  │                   │ MCP Server  │
            │  (stdio)    │                   │ (pooled)    │
            └─────────────┘                   └──────┬──────┘
                                                     │
                                              ┌──────▼──────┐
                                              │ PostgreSQL  │
                                              └─────────────┘

     state lives in RAM                   state lives in the database
     one user                             many users
     one global GitHub token              one token PER USER
     approval blocks on input()           approval is asynchronous
```

**The Agent Engine itself barely changes.** That was the point of
building it stateless: `route()` and `execute()` take everything as
arguments and return values, so one instance can serve many concurrent
requests safely. Phase 3 wraps it, it does not rewrite it.

---

# ⚠️ Three hard problems — read this before writing code

Most Phase 3 tutorials skip these and you discover them at week three.
All three come from your **actual current code**.

## Problem 1 — the MCP session cannot be per-request

Today `ai_client.py` does this:

```python
server_params = StdioServerParameters(command="python", args=["server.py"])

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
```

That spawns a **subprocess** and keeps it for the whole CLI session.

In FastAPI, doing that per request means:

```text
100 concurrent users
        ↓
100 python subprocesses
        ↓
100 x (process spawn + MCP handshake + OAuth)
        ↓
your server falls over
```

### Three options

```text
OPTION A — one shared session, guarded by a lock
  + simplest, no protocol change
  - serialises every tool call across all users
  - one crash kills everyone
  Use for: MVP, low traffic

OPTION B — a small pool of stdio sessions
  + real concurrency, bounded resources
  - you must write health-checking and recycling
  Use for: production with a single-tenant MCP server

OPTION C — switch the MCP server to HTTP/SSE transport
  + horizontally scalable, restartable, standard
  - the MCP server becomes a real service you must deploy
  Use for: the end state
```

**Recommendation: start with A, design for C.** Put the session behind
an interface from day one:

```python
class MCPSessionProvider(Protocol):
    async def acquire(self, user_id: str) -> ClientSession: ...
    async def release(self, session: ClientSession) -> None: ...
```

Then A → B → C is a swap of one class, exactly like
`EmbeddingProvider` and `PermissionPolicy` in Phase 2.

## Problem 2 — your MCP server is single-tenant

This is the biggest one, and it is easy to miss until a second user
signs up.

```python
# services/github/services.py
self.token = os.getenv("GITHUB_TOKEN")     # ← ONE token, globally

# services/slack/services.py
self.token = token or os.getenv("SLACK_BOT_TOKEN")

# services/google_drive/services.py
self.token_path = resolve_path(settings.google_token_path)   # ← ONE file
```

Every user of Agent Hub would share **your** GitHub account. That is
not a bug you can ship around.

### Three ways to fix it

```text
APPROACH 1 — credentials per call
  The backend passes the user's token as an MCP tool argument
  or request metadata; the service layer uses it instead of env.

  + one MCP server serves everyone
  + cheap
  - every tool signature changes
  - tokens travel through the MCP layer (encrypt in transit)

APPROACH 2 — one MCP server process per connected user
  + perfect isolation, zero service-layer changes
  - a process per user does not scale past a few dozen

APPROACH 3 — a credential resolver inside the MCP server
  The backend sends a user id in MCP request metadata (`_meta`).
  The service layer looks the token up from a store.

  + tool signatures unchanged
  + tokens never enter the LLM's context
  - the MCP server needs database or vault access
```

**Recommendation: Approach 3.** It keeps credentials out of tool
arguments — which matters because tool arguments are visible to the
LLM and get written into `ExecutionRecord.arguments`.

> This work is scheduled in **Phase 5**. In Phase 3, build the
> `plugin_connections` table and the encryption, but keep running
> single-tenant behind a feature flag. Do not let it block the API.

## Problem 3 — approval cannot block

Phase 2's `ConsoleApproval` calls `input()` inside
`asyncio.to_thread`. In a web request there is no terminal, and an
HTTP request cannot wait ten minutes for a human.

```text
     CLI (Phase 2)                 WEB (Phase 3)

  executor reaches step 5      executor reaches step 5
         ↓                              ↓
     input() blocks             persist a PendingApproval row
         ↓                              ↓
   user types y                 return "awaiting_approval" to client
         ↓                              ↓
     continue                   user clicks Approve (separate request)
                                        ↓
                                 RESUME the agent turn
```

The agent turn becomes **suspendable**. Two ways:

```text
SHORT-LIVED  keep the turn alive in memory and await an asyncio.Event
             that the approval endpoint sets.
             + simple    - dies on restart, one worker only

DURABLE      persist the turn state, return, and re-enter the loop on
             approval.
             + survives restarts and scales    - more work
```

Start short-lived with a timeout (e.g. 5 minutes), make it durable in
Phase 6 when Redis arrives.

---

# Phase 3.1 — Project structure

Keep `agent/` untouched. Add `api/` beside it.

```text
personal-mcp-server/
│
├── agent/                  ← Phase 2, unchanged
├── services/               ← MCP tool implementations
├── core/
├── config/
├── server.py               ← the MCP server
│
├── api/                    ← NEW: everything Phase 3
│   ├── main.py             app factory + lifespan
│   ├── deps.py             shared dependencies
│   ├── settings.py         API-specific config
│   │
│   ├── db/
│   │   ├── base.py         SQLAlchemy Base + engine
│   │   ├── session.py      async session factory
│   │   └── models/
│   │       ├── user.py
│   │       ├── agent.py
│   │       ├── plugin.py
│   │       ├── conversation.py
│   │       └── execution.py
│   │
│   ├── schemas/            Pydantic request/response models
│   ├── routers/
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── plugins.py
│   │   ├── agents.py
│   │   ├── conversations.py
│   │   └── chat.py
│   │
│   ├── services/           business logic (not HTTP, not SQL)
│   │   ├── auth_service.py
│   │   ├── agent_service.py
│   │   └── chat_service.py
│   │
│   └── mcp/
│       ├── provider.py     MCPSessionProvider implementations
│       └── lifecycle.py    startup/shutdown wiring
│
├── alembic/                migrations
└── tests/
    ├── api/
    ├── router/             ← Phase 2
    └── executor/           ← Phase 2
```

> **Why `api/services/` separate from routers?**
> Routers should only do HTTP: parse, validate, call, serialize. Put
> the logic one layer down and you can test it without a web client,
> and reuse it later from a CLI, a worker, or a scheduled job.

---

# Phase 3.2 — Database model

Start with **seven tables**. Not fifty.

```text
users
  id, email, password_hash, full_name, is_active,
  email_verified_at, created_at, updated_at

plugin_connections                (a user's link to one service)
  id, user_id → users
  plugin_key           'github' | 'google_drive' | 'slack' | ...
  status               connected | expired | revoked
  account_label        'user@example.com'
  credentials_enc      BYTEA - encrypted, never plaintext
  scopes               TEXT[]
  connected_at, last_used_at, expires_at

agents
  id, user_id → users
  name, description, avatar_url
  system_prompt
  model                'minimax-m3:cloud'
  temperature
  is_archived, created_at, updated_at

agent_tools                       (which tools this agent may use)
  id, agent_id → agents
  tool_name            'github_create_issue'
  namespace            'github'
  enabled              bool
  requires_approval    bool - user override of the default

conversations
  id, agent_id → agents, user_id → users
  title, is_archived
  created_at, updated_at, last_message_at

messages
  id, conversation_id → conversations
  role                 system | user | assistant | tool
  content              TEXT
  tool_name            for role='tool'
  routing              JSONB - RoutingDecision.to_dict()
  token_usage          JSONB
  created_at

tool_executions
  id (= ExecutionRecord.execution_id)
  message_id → messages, conversation_id, agent_id, user_id
  tool_name, server, namespace, operation, risk_level
  status               success | failed | denied
  started_at, duration_ms, attempts
  arguments            JSONB - already redacted by Phase 2
  coercions            TEXT[]
  approved_by_user     bool NULL
  error                JSONB
```

## This maps 1:1 to what Phase 2 already produces

You do not have to invent these shapes — they exist:

```python
ExecutionRecord.to_dict()   →  one row in tool_executions
RoutingDecision.to_dict()   →  messages.routing
ToolDefinition.permissions  →  agent_tools + Phase 5 scopes
```

That is the payoff for building Phase 2.8 telemetry properly.

## Relationships

```text
User
 ├── PluginConnection      (github, drive, slack, calendar)
 └── Agent
       ├── AgentTool       (per-tool enable + approval override)
       └── Conversation
             └── Message
                   └── ToolExecution
```

## Indexes you will want on day one

```sql
CREATE INDEX ix_conversations_agent_last  ON conversations (agent_id, last_message_at DESC);
CREATE INDEX ix_messages_conversation     ON messages (conversation_id, created_at);
CREATE INDEX ix_executions_user_started   ON tool_executions (user_id, started_at DESC);
CREATE INDEX ix_executions_status         ON tool_executions (status) WHERE status <> 'success';
CREATE UNIQUE INDEX ux_connection_plugin  ON plugin_connections (user_id, plugin_key);
```

The partial index on failures is deliberate: the executions page is
almost always filtered to "what broke".

---

# Phase 3.3 — Migrations

Use **Alembic from the first table**, never `create_all()`.

```bash
alembic init -t async alembic
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Rules that save pain later:

```text
1. Every schema change is a migration. No exceptions.
2. Migrations run in CI against a scratch database.
3. Never edit a migration that has been applied anywhere real.
4. Autogenerate is a DRAFT - read every generated migration.
5. Write the downgrade. You will need it at 2am one day.
```

---

# Phase 3.4 — Authentication

```text
POST /api/auth/register     email + password → user + tokens
POST /api/auth/login        credentials      → access + refresh
POST /api/auth/refresh      refresh token    → new access token
POST /api/auth/logout       revoke refresh token
GET  /api/auth/me           current user
```

### Token strategy

```text
ACCESS TOKEN
  JWT, 15 minutes, stateless
  claims: sub (user id), exp, iat, jti, type='access'

REFRESH TOKEN
  opaque random string, 30 days
  stored HASHED in the database so it can be revoked
  rotated on every use
```

> **Why store refresh tokens but not access tokens?**
> A stateless access token cannot be revoked — that is the trade for
> not hitting the database on every request. Keeping it short-lived
> bounds the damage. The refresh token is the thing worth revoking, so
> it lives in the database, hashed like a password.

### Password hashing

```text
argon2id     (preferred)
bcrypt       (fine, widely supported)

NEVER: md5, sha1, sha256, or any unsalted hash
```

### Delivery

```text
Browser  →  refresh token in an httpOnly, Secure, SameSite=Lax cookie
            access token in memory (never localStorage)

API/CLI  →  Authorization: Bearer <access token>
```

`localStorage` is readable by any injected script. An httpOnly cookie
is not.

---

# Phase 3.5 — Plugins and connections

```text
GET    /api/plugins                      catalogue (static for now)
GET    /api/plugins/{key}                detail + tool list
GET    /api/plugins/connections          this user's connections
POST   /api/plugins/{key}/connect        begin connection
DELETE /api/plugins/{key}/connect        disconnect
```

The catalogue is derived from the **live registry**, not hardcoded:

```python
engine.registry.stats()          # {'github': 18, 'slack': 14, ...}
engine.registry.by_namespace()   # full ToolDefinition list
```

So a plugin's tool list, risk levels and permission scopes all come
from Phase 2 automatically. Add a service to the MCP server and it
appears in the API with no backend change.

### Credential storage

```text
credentials_enc BYTEA        ← encrypted at rest, always
```

```python
from cryptography.fernet import Fernet     # already in requirements

# key from environment, NEVER in code or the database
fernet = Fernet(settings.credential_encryption_key)

encrypted = fernet.encrypt(json.dumps(tokens).encode())
```

Design so this can move to a real secret manager later: keep all
encrypt/decrypt behind one `CredentialStore` class. Then AWS Secrets
Manager or Vault is a new implementation, not a migration.

> Full OAuth flows are **Phase 5**. In Phase 3, `connect` can accept a
> pasted personal access token. That is enough to build and test
> everything else.

---

# Phase 3.6 — Agents

```text
GET    /api/agents                 list
POST   /api/agents                 create
GET    /api/agents/{id}            detail + tools
PATCH  /api/agents/{id}            update
DELETE /api/agents/{id}            archive (soft delete)
GET    /api/agents/{id}/tools      available + enabled tools
PUT    /api/agents/{id}/tools      set enabled tools
```

### Create payload

```json
{
  "name": "Developer Agent",
  "description": "Helps with repositories and documentation",
  "system_prompt": "You are a senior software engineer...",
  "model": "minimax-m3:cloud",
  "plugins": ["github", "google_drive"],
  "tools": {
    "github_list_issues":   { "enabled": true },
    "github_create_issue":  { "enabled": true, "requires_approval": true },
    "github_search_code":   { "enabled": true }
  }
}
```

### Sensible defaults when tools are not specified

Use the Phase 2 classification:

```python
enabled           = tool.read_only          # reads on, writes off
requires_approval = tool.requires_approval  # already computed
```

A new agent is safe by default and the user opts into writes. That is
the correct direction for a product handling someone's real accounts.

### Soft delete

Never hard-delete an agent — conversations and executions reference
it, and users delete things by accident. `is_archived` plus a filtered
default query is enough.

---

# Phase 3.7 — Conversations and messages

```text
GET    /api/agents/{id}/conversations
POST   /api/agents/{id}/conversations
GET    /api/conversations/{id}
PATCH  /api/conversations/{id}          rename
DELETE /api/conversations/{id}
GET    /api/conversations/{id}/messages  paginated
```

### Rebuilding history for the LLM

Phase 2's `run_agent` takes a `messages` list. Rebuild it per request:

```python
messages = [{"role": "system", "content": agent.system_prompt}]

for row in recent_messages(conversation_id, limit=40):
    messages.append({"role": row.role, "content": row.content})
```

### Two things that will bite you

```text
1. CONTEXT WINDOW
   A 200-message conversation will not fit. Cap by TOKENS, not by
   message count, and always keep the system prompt plus the most
   recent turns. Summarise the middle later (Phase V2 memory).

2. TOOL MESSAGES
   role='tool' rows are large and mostly noise after the fact. Store
   them, but consider excluding older ones when rebuilding context -
   the assistant's summary of a result is usually enough.
```

---

# Phase 3.8 — The chat endpoint ⭐

This is where Phase 2 pays off.

```text
POST /api/conversations/{id}/messages
```

```text
1. authenticate         → user
2. load                 → conversation, agent, agent_tools
3. persist              → the user's message
4. acquire              → an MCP session (Problem 1)
5. route                → engine.route(text, previous_namespaces=...)
6. filter               → intersect with this agent's enabled tools
7. run                  → run_agent(...) with a per-agent executor
8. persist              → assistant message + every ExecutionRecord
9. return               → answer + timeline
```

### Step 6 is the important new one

The router picks what is *relevant*. The agent config decides what is
*allowed*. Both apply:

```python
decision = engine.route(text, previous_namespaces=previous)

enabled = {t.tool_name for t in agent_tools if t.enabled}

tools = [
    tool
    for tool in engine.select_mcp_tools(decision)
    if tool.name in enabled
]
```

And the executor gets a policy built from the same data:

```python
executor = ToolExecutor(
    engine.registry,
    policy=AgentToolPolicy(enabled),       # implements PermissionPolicy
    approval=WebApproval(conversation_id), # implements ApprovalHandler
)
```

> Both are just new classes implementing the Phase 2 protocols. The
> executor does not change at all. That is what the seams were for.

### Persisting the result

```python
for record in turn.executions:
    db.add(ToolExecutionRow(**record.to_dict(), message_id=msg.id))
```

`ExecutionRecord.to_dict()` already matches the table. No mapping code.

---

# Phase 3.9 — Streaming with SSE

A tool-using turn takes 5–30 seconds. A silent spinner feels broken.

```text
GET /api/conversations/{id}/stream        (Server-Sent Events)

event: routing
data: {"services": ["github"], "tools": 8, "confidence": 0.93}

event: tool_start
data: {"execution_id": "exec_a1b2", "tool": "github_list_issues"}

event: tool_end
data: {"execution_id": "exec_a1b2", "status": "success", "duration_ms": 421}

event: approval_required
data: {"approval_id": "apr_9x8", "tool": "github_create_issue", ...}

event: token
data: {"text": "You have 3 open "}

event: done
data: {"message_id": "msg_77", "answer": "..."}
```

### Why SSE and not WebSockets

```text
SSE                              WebSockets
─────────────────────────────    ─────────────────────────────
one-way (server → client)        two-way
plain HTTP, works everywhere     needs upgrade support in proxies
auto-reconnect built in          you write reconnection
trivial to load-balance          sticky sessions usually needed
```

Everything you need to push is server → client. The user's input is a
normal POST. **SSE is the right default; revisit only if you add
collaborative editing.**

---

# Phase 3.10 — API conventions

### Error shape — one, everywhere

```json
{
  "error": {
    "code": "tool_permission_denied",
    "message": "This agent may not use github_create_issue.",
    "details": { "tool": "github_create_issue" },
    "request_id": "req_c4f8a1"
  }
}
```

Reuse the Phase 2 `ErrorCode` vocabulary where it applies. One
vocabulary from executor to HTTP means the frontend learns one set of
codes.

### Status codes

```text
200  fine            401  not authenticated
201  created         403  authenticated, not allowed
202  accepted        404  not found (or not yours - do not leak)
204  no content      409  conflict
                     422  validation failed
                     429  rate limited
                     500  our fault
```

### Pagination

Cursor-based, not offset. Offset pagination shifts when rows are
inserted, and chat inserts constantly.

```json
{ "items": [...], "next_cursor": "eyJpZCI6...", "has_more": true }
```

### Request IDs

Generate one per request, log it, return it in every response and
error. When a user reports a bug, that id is the whole investigation.

---

# Phase 3.11 — Testing

```text
tests/api/
├── conftest.py           test database, client, factories
├── test_auth.py          register, login, refresh, revoke
├── test_agents.py        CRUD + ownership isolation
├── test_plugins.py       connect, disconnect, catalogue
├── test_conversations.py history, pagination
├── test_chat.py          the full turn, with a fake MCP session
└── test_security.py      cross-user access, IDOR
```

### The tests that matter most

```python
def test_user_cannot_read_another_users_agent():
    # The single most common security bug in CRUD apps: an id in the
    # URL and no ownership check.
    ...

def test_agent_cannot_use_a_tool_it_was_not_granted():
    # The router may suggest it; the policy must still refuse.
    ...

def test_chat_persists_every_execution():
    # The timeline is a product feature. A missing row is a missing
    # feature.
    ...
```

### Reuse the Phase 2 approach

`tests/tool_fixtures.py` builds tool definitions from the real service
source with `ast` — no credentials, no network. Do the same here: a
fake MCP session (like `FakeSession` in `tests/executor/`) means the
whole chat endpoint is testable without GitHub, Slack or Google.

---

# One thing I would NOT build yet

```text
❌ OAuth flows            → Phase 5 (paste a token for now)
❌ Multi-tenant MCP       → Phase 5
❌ Redis / Celery         → Phase 6
❌ WebSockets             → SSE is enough
❌ GraphQL                → REST is enough
❌ Microservices          → one FastAPI app is enough
❌ Agent memory           → V2
❌ Marketplace            → V2
```

You already have the hard part working. Phase 3 is plumbing — do it
plainly and get to a usable product.

---

# Phase 3 milestone

Phase 3 is complete when this sequence works over HTTP:

### Test 1 — a user exists

```text
POST /api/auth/register  →  201, tokens returned
POST /api/auth/login     →  200, tokens returned
GET  /api/auth/me        →  200, correct user
```

### Test 2 — a plugin is connected

```text
POST /api/plugins/github/connect   →  201
GET  /api/plugins/connections      →  github: connected
```

Credentials are encrypted in the database. Verify by reading the row
directly — you must not be able to see the token.

### Test 3 — an agent exists

```text
POST /api/agents  →  201, id returned
GET  /api/agents/{id}/tools  →  reads enabled, writes disabled
```

### Test 4 — a conversation produces a real answer

```text
POST /api/agents/{id}/conversations         →  201
POST /api/conversations/{id}/messages
     { "content": "List my open GitHub issues" }
     →  200
     →  answer text
     →  timeline with >= 1 tool_execution row
```

Then confirm in the database:

```sql
SELECT tool_name, status, duration_ms FROM tool_executions
ORDER BY started_at DESC LIMIT 5;
```

### Test 5 — isolation holds

```text
user A creates agent X
user B requests GET /api/agents/X    →  404 (not 403 - do not confirm
                                          that X exists)
```

### Test 6 — the stream works

```text
GET /api/conversations/{id}/stream
  → routing event
  → tool_start / tool_end events
  → done event
```

**If all six pass, Phase 3 is genuinely done.**

---

## 🎯 Your next immediate task

Build in this order — each step is usable before the next begins:

```text
1.  api/ skeleton + FastAPI app factory
        ↓
2.  SQLAlchemy models + first Alembic migration
        ↓
3.  Auth (register / login / refresh / me)
        ↓
4.  MCPSessionProvider (shared-session version)
        ↓
5.  AgentEngine as an application singleton (lifespan)
        ↓
6.  Plugin catalogue from the live registry
        ↓
7.  Agent CRUD + tool selection
        ↓
8.  Conversations + messages
        ↓
9.  The chat endpoint (non-streaming first)
        ↓
10. Persist ExecutionRecords
        ↓
11. SSE streaming
        ↓
12. API tests with a fake MCP session
```

> **Do not skip step 4.** The session provider is the thing that makes
> steps 9–11 possible. Getting it wrong means rewriting the chat
> endpoint twice.

---

## Related documents

| Document | What it covers |
|---|---|
| `phases_doc/Phase2.md` | Agent Engine — routing and execution |
| `devloper_docs/Understand_Phse2.md` | Beginner walkthrough of Phase 2 |
| `phases_doc/Phase4.md` | Frontend |
| `phases_doc/Phase5.md` | Permissions, OAuth, multi-tenancy |
| `phases_doc/Phase6.md` | Docker, CI/CD, observability |
