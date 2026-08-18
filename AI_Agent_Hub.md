# Agent Hub — Product Requirements

> **This document is the product vision.**
> The build plan, phase by phase, lives in
> [`phases_doc/`](phases_doc/README.md).
>
> | | |
> |---|---|
> | **Phase 1** — MCP server | Complete — 61 tools across 4 services |
> | **Phase 2** — Agent Engine | Complete — 147 tests |
> | **Phase 3** — Backend | Next |
> | **Phases 4-6** | Planned |
>
> **Start here to build:** [`phases_doc/Phase3.md`](phases_doc/Phase3.md)
> **Start here to understand the code:** [`devloper_docs/Understand_Phse2.md`](devloper_docs/Understand_Phse2.md)

---

## Where the plan has already changed

Two corrections to the original document, both from building it:

**More plugins than planned.** This document says GitHub + Drive are
enough for V1. Phase 1 shipped **four** services and **61 tools**:

```text
GitHub            18 tools
Google Calendar   16 tools
Slack             14 tools
Google Drive      13 tools
```

That turned out to be a benefit — 61 tools across 4 services is a real
test of the routing architecture in a way that 2 services would not
have been. Multi-service requests, namespace collisions (three
services expose a `get_file`) and tool-budget pressure all surfaced
early, while they were cheap to fix.

**Section 25's folder layout was not followed.** The engine lives in
`agent/` in this repository rather than a separate `agent-engine/`
tree, and it grew modules the original sketch did not anticipate
(`lexicon.py`, `index.py`, `classification.py`, `execution.py`).
`planner.py` proved unnecessary. See
[`phases_doc/Phase2.md`](phases_doc/Phase2.md#what-actually-shipped)
for the delivered structure.

---

## 1. Product vision

```text
                         AGENT HUB
                            │
             ┌──────────────┴──────────────┐
             │                             │
        Connect Plugins              Create Agents
             │                             │
      ┌──────┼──────┐                       │
      ▼      ▼      ▼                       ▼
   GitHub  Drive  Slack ...        Configure Agent
                                      │
                                      ▼
                                   AI Agent
                                      │
                              ┌───────┼───────┐
                              ▼       ▼       ▼
                           GitHub   Drive    Slack ...
                              │       │       │
                              └───────┼───────┘
                                      ▼
                                  AI Result
```

The platform should hide MCP complexity from normal users.

A user should think:

> "Connect GitHub → create developer agent → ask it to analyze my repository."

Not:

> "I need to understand MCP protocol and manually configure an MCP client."

---

# 2. V1 — Minimum production-worthy product

I strongly recommend making this your first milestone.

### V1 functionality

| Module             | Functionality                   |
| ------------------ | ------------------------------- |
| Authentication     | Register/login/logout           |
| Dashboard          | Overview of agents/plugins      |
| Plugin system      | GitHub + Drive                  |
| Plugin connection  | Connect/disconnect accounts     |
| Agent builder      | Create/configure agent          |
| Tool selection     | Choose tools available to agent |
| Agent chat         | Talk to agent                   |
| MCP client         | Discover/call MCP tools         |
| Tool execution     | Execute selected MCP tools      |
| Execution timeline | Show tools being called         |
| Conversations      | Store chat history              |
| Agent management   | Edit/delete agents              |
| Basic permissions  | Control read/write tools        |

This is enough to make a **real usable product**.

---

# 3. Authentication system

Users need accounts.

### Required

```text
Register
Login
Logout
Refresh session
Password reset
Email verification
```

For V1 you can keep it simple.

### Recommended

```text
JWT access token
+
Refresh token
```

or secure HTTP-only session cookies.

### Future

```text
Google OAuth
GitHub OAuth
Microsoft OAuth
```

---

# 4. User dashboard

After login:

```text
┌──────────────────────────────────────────────┐
│ Agent Hub                         User ▼     │
├──────────────────────────────────────────────┤
│                                              │
│  Welcome back 👋                             │
│                                              │
│  Agents       Plugins       Tasks            │
│    3             2            42             │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ Developer Agent                         │ │
│ │ GitHub + Drive                          │ │
│ │ Last used: Today                        │ │
│ │                         [Open Agent]     │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│              [+ Create Agent]                │
└──────────────────────────────────────────────┘
```

Dashboard should show:

* total agents
* connected plugins
* recent conversations
* recent executions
* failed executions
* quick-create agent

---

# 5. Plugin system ⭐

This is the foundation of Agent Hub.

You should **not hardcode GitHub and Drive into the frontend**.

Instead create a generic plugin architecture.

```text
Plugin
│
├── id
├── name
├── description
├── icon
├── version
├── category
├── MCP endpoint/config
├── authentication type
└── tools
```

Example:

```text
GitHub Plugin
     │
     ├── search_repositories
     ├── list_issues
     ├── create_issue
     ├── list_pull_requests
     └── ...
```

Drive:

```text
Google Drive Plugin
     │
     ├── search_files
     ├── get_file
     ├── create_file
     └── ...
```

---

# 6. Plugin connection system

A user should be able to:

```text
Plugins
   ↓
GitHub
   ↓
[Connect]
```

Then OAuth/authentication happens.

After connection:

```text
GitHub

● Connected

Account:
user@example.com

[Manage] [Disconnect]
```

Same for Drive.

### Store

You need to maintain:

```text
User
Plugin
UserPluginConnection
Credentials / OAuth tokens
Connection status
```

### Important

Do **not** store OAuth secrets as plain text.

Use:

```text
Encryption
+
Secret management
```

Even for V1, design the database so credentials can later move to a proper secret manager.

---

# 7. Agent Builder ⭐⭐⭐⭐⭐

This is the main feature of Agent Hub.

User clicks:

**Create Agent**

They configure:

### Basic information

```text
Name
Description
Avatar
```

### System instructions

```text
You are a senior software engineer.
Help users understand and manage their repositories.
Never perform destructive actions without confirmation.
```

### Model

Initially:

```text
Ollama
```

Later:

```text
OpenAI
Anthropic
Google
Groq
OpenRouter
```

Don't couple your Agent Engine to one provider.

Create:

```text
LLMProvider interface
```

so models can be swapped.

---

# 8. Agent plugin selection

Example:

```text
Available Plugins

☑ GitHub
☑ Google Drive
☐ Slack
☐ Notion
```

The agent only gets tools from selected plugins.

Architecture:

```text
Agent
 │
 ├── GitHub
 │    ├── search
 │    ├── issues
 │    └── PRs
 │
 └── Drive
      ├── search
      └── read
```

---

# 9. Tool-level permissions ⭐⭐⭐⭐⭐

This is very important for a production-looking project.

Don't only allow:

```text
GitHub = ON
```

Allow individual tools.

Example:

```text
GitHub

READ
☑ Search repositories
☑ Read issues
☑ Read PRs
☑ Read files

WRITE
☑ Create issue
☐ Close issue
☐ Merge PR
☐ Delete repository
```

Your system becomes:

```text
Agent
 ↓
Tool Request
 ↓
Permission Engine
 ↓
Allowed?
 ├── YES → Execute
 └── NO  → Reject
```

---

# 10. MCP Client / Agent Engine ⭐⭐⭐⭐⭐

This is where your existing MCP work becomes useful.

Your Agent Engine should:

```text
Receive user message
        ↓
Build context
        ↓
Discover available tools
        ↓
Give relevant tools to LLM
        ↓
LLM decides tool
        ↓
Validate permission
        ↓
Execute MCP tool
        ↓
Return result
        ↓
LLM generates response
```

Example:

```text
User:
Find my open GitHub issues.

        ↓

Agent

        ↓

github.list_issues()

        ↓

MCP Server

        ↓

GitHub API

        ↓

Result

        ↓

Agent

        ↓

Response
```

---

# 11. Intelligent tool routing

You already have experience with this from your existing MCP client.

Don't send every tool to the LLM when you eventually have hundreds.

Build:

```text
User Request
     ↓
Tool Router
     ↓
Relevant Tools
     ↓
LLM
```

For example:

> "Find my files about MCP."

Router selects:

```text
drive.search_files
drive.get_file
```

instead of sending:

```text
GitHub 30 tools
Drive 20 tools
Slack 20 tools
Jira 30 tools
...
```

This improves:

* latency
* token usage
* tool accuracy
* scalability

---

# 12. Agent chat interface

This is the main user interface.

```text
┌─────────────────────────────────────────────┐
│ Developer Agent                             │
│ GitHub ●   Drive ●                          │
├─────────────────────────────────────────────┤
│                                             │
│ You                                         │
│ Analyze my latest authentication PR.        │
│                                             │
│ Agent                                       │
│ I'll inspect the PR and related documents.  │
│                                             │
│ ┌─ Tool execution ───────────────────────┐  │
│ │ ✓ github.search_pull_requests           │  │
│ │ ✓ github.get_pull_request               │  │
│ │ ✓ drive.search_files                    │  │
│ │ ✓ drive.get_file                        │  │
│ └─────────────────────────────────────────┘  │
│                                             │
│ Analysis...                                 │
│                                             │
├─────────────────────────────────────────────┤
│ Ask your agent...                      [➤] │
└─────────────────────────────────────────────┘
```

---

# 13. Tool execution timeline

This is worth building even in V1.

Show:

```text
● Thinking
│
● Searching GitHub
│
● Reading PR #124
│
● Searching Drive
│
● Reading architecture document
│
● Analyzing
│
● Completed
```

For each tool:

```text
Tool:
github.get_pull_request

Status:
SUCCESS

Duration:
482 ms
```

This is excellent for debugging too.

---

# 14. Human approval system

For dangerous operations:

```text
Agent wants to:

Create GitHub issue

Title:
Authentication token expires too early

[Cancel] [Approve]
```

For V1, implement this for **write tools**.

Recommended rule:

```text
READ
→ automatic

WRITE
→ approval required
```

Later you can let users configure it.

---

# 15. Conversation management

Store:

```text
Conversation
Message
ToolExecution
Agent
User
```

Example:

```text
Agent
 ├── Conversation 1
 │    ├── Message
 │    ├── ToolExecution
 │    └── Message
 │
 └── Conversation 2
      ├── Message
      └── ToolExecution
```

Users should be able to:

```text
New conversation
Rename conversation
Delete conversation
Continue conversation
```

---

# 16. Agent memory

Don't make sophisticated long-term memory part of V1.

But design for it.

Eventually:

```text
Short-term memory
Long-term memory
User preferences
Project context
Task memory
```

Potential storage:

```text
PostgreSQL
+
pgvector
```

---

# 17. Agent execution history

Add an execution page:

```text
Executions

Today

✓ GitHub search                 320ms
✓ Drive search                  540ms
✓ GitHub issue created          410ms
✗ Drive file upload             1.2s

Yesterday

✓ Repository analysis           2.1s
```

Useful for both users and administrators.

---

# 18. Plugin marketplace — V2

Don't build this first.

Eventually:

```text
Plugin Marketplace

GitHub             Installed
Google Drive       Installed
Slack              Install
Notion             Install
Jira                Install
PostgreSQL          Install
```

Each plugin has:

```text
Name
Description
Developer
Version
Tools
Permissions
Rating
Install count
```

---

# 19. Custom MCP server support — V2

This is one of the most powerful future features.

Allow users to connect their own MCP server.

For example:

```text
Add Custom MCP Server

Name:
My Company CRM

Connection:
https://example.com/mcp

Authentication:
OAuth / API Key

[Connect]
```

Now Agent Hub isn't limited to your plugins.

---

# 20. Workflow automation — V2/V3

Eventually:

```text
Workflow

GitHub PR created
        ↓
Analyze PR
        ↓
Search Drive documentation
        ↓
Generate review
        ↓
Approval
        ↓
Post Slack message
```

Support:

* sequential steps
* parallel execution
* conditions
* retries
* scheduled execution
* webhooks

---

# 21. Multi-agent system — V3

Eventually:

```text
                Manager Agent
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
      Developer   Researcher  Reviewer
        Agent       Agent       Agent
          │          │           │
       GitHub      Drive       GitHub
```

Don't build this initially.

---

# 22. Backend requirements

For your background, I recommend:

### Backend

**FastAPI**

Why?

```text
Async
WebSockets
Good API architecture
Easy MCP integration
Pydantic
Fast development
```

You can use Django if you prefer its ORM/admin ecosystem, but for this particular project I'd lean toward FastAPI.

### Database

**PostgreSQL**

Store:

```text
users
agents
plugins
connections
conversations
messages
tool_executions
permissions
workflows
```

### Cache / queue

**Redis**

Later:

```text
Task queue
Caching
Rate limiting
Session state
Agent execution state
```

---

# 23. Frontend requirements

I'd use:

### Next.js + TypeScript

UI:

```text
Next.js
TypeScript
Tailwind CSS
shadcn/ui
```

Important pages:

```text
/login
/register

/dashboard

/plugins
/plugins/[plugin]

/agents
/agents/create
/agents/[id]

/agents/[id]/chat

/executions

/settings
```

---

# 24. Real-time communication

For agent execution:

```text
Frontend
   ↕
WebSocket / SSE
   ↕
Backend
```

So the UI can show:

```text
Searching GitHub...
Reading Drive...
Calling tool...
Generating response...
```

in real time.

For V1, **SSE is probably enough** and simpler than full WebSockets.

---

# 25. MCP infrastructure

Your existing MCP server becomes:

```text
mcp-server/
│
├── services/
│   ├── github/
│   └── drive/
│
├── core/
├── server.py
└── ...
```

Agent Hub contains:

```text
agent-engine/
│
├── mcp/
│   ├── client.py
│   ├── registry.py
│   └── executor.py
│
├── agents/
│   ├── planner.py
│   ├── router.py
│   ├── memory.py
│   └── runner.py
│
├── permissions/
├── approvals/
└── providers/
```

---

# 26. Security requirements

This is important if you're calling the project "production level."

### Authentication

```text
JWT/session
OAuth
```

### Authorization

```text
User
 ↓
Agent
 ↓
Plugin
 ↓
Tool
```

### Credential security

Never expose:

```text
GitHub token
Google OAuth token
API keys
```

to the frontend.

### Other protections

```text
Input validation
Rate limiting
CORS
CSRF where applicable
Encryption
Audit logging
Tool allowlists
Command restrictions
Prompt injection defenses
```

---

# 27. Observability — V2

Add:

```text
Structured logging
Request ID
Agent execution ID
Tool execution ID
Latency
Error tracking
```

Eventually:

```text
Prometheus
Grafana
OpenTelemetry
```

Dashboard:

```text
Requests             12,482
Tool Calls            32,901
Success Rate            98.7%
Average Latency         430ms
Failed Tools              421
```

---

# 28. Testing requirements

Don't skip this.

### Backend

```text
Unit tests
Integration tests
API tests
```

### MCP

Test:

```text
Tool discovery
Tool execution
Invalid arguments
Authentication failure
Permission failure
Timeout
Retry
```

### Agent

Test:

```text
Correct tool selection
Multiple tool calls
Tool failure recovery
Permission handling
Approval flow
```

### Frontend

At minimum:

```text
Login
Plugin connection
Agent creation
Chat
Tool execution
```

---

# 29. Deployment requirements

For your portfolio:

```text
Docker
Docker Compose
```

Services:

```text
frontend
backend
mcp-server
postgres
redis
```

Architecture:

```text
                    Internet
                       │
                       ▼
                  Reverse Proxy
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
        Next.js                FastAPI
                                  │
                       ┌──────────┼──────────┐
                       ▼          ▼          ▼
                    MCP       PostgreSQL   Redis
                   Server
```

Later:

```text
CI/CD
GitHub Actions
Cloud deployment
HTTPS
Secrets management
Monitoring
```

---

# 30. Technology stack I'd choose

| Layer               | Technology                 |
| ------------------- | -------------------------- |
| Frontend            | Next.js                    |
| Language            | TypeScript                 |
| UI                  | Tailwind + shadcn/ui       |
| Backend             | FastAPI                    |
| Backend language    | Python                     |
| MCP                 | Python MCP SDK             |
| Agent               | Your existing Ollama agent |
| LLM abstraction     | Provider interface         |
| Database            | PostgreSQL                 |
| ORM                 | SQLAlchemy                 |
| Validation          | Pydantic                   |
| Cache               | Redis                      |
| Real-time           | SSE                        |
| Authentication      | JWT/session + OAuth        |
| Containerization    | Docker                     |
| CI/CD               | GitHub Actions             |
| Testing             | Pytest + Playwright        |
| Logging             | Python structured logging  |
| Monitoring later    | Prometheus + Grafana       |
| Tracing later       | OpenTelemetry              |
| Vector memory later | pgvector                   |

---

# 🧩 Core database model

For V1, don't create 50 tables.

Start with:

```text
User
 │
 ├── Agent
 │     │
 │     ├── AgentPlugin
 │     │
 │     └── Conversation
 │              │
 │              └── Message
 │
 └── PluginConnection
```

And:

```text
ToolExecution
```

linked to messages.

Later add:

```text
Permission
Approval
Workflow
WorkflowExecution
Memory
PluginVersion
MarketplacePlugin
```

---

# 🔌 Your first plugins

Since you already have them:

### Plugin #1

**GitHub MCP**

Use your existing implementation.

### Plugin #2

**Google Drive MCP**

Finish your current implementation.

Then V2:

```text
Slack
Google Calendar
Gmail
Notion
Jira
PostgreSQL
```

Don't add all of these now.

**GitHub + Drive are enough to prove the architecture.**

---

# 🎯 V1 exact scope I recommend

If you want to move fast, freeze V1 at this:

```text
                    AGENT HUB V1

Authentication
      │
      ▼
Dashboard
      │
      ├──────────────┐
      ▼              ▼
  Plugins          Agents
      │              │
 GitHub            Create
 Drive             Configure
      │              │
      └──────┬───────┘
             ▼
          MCP Client
             │
             ▼
        Tool Router
             │
             ▼
       Permission Check
             │
             ▼
        Tool Execution
             │
             ▼
         AI Response
             │
             ▼
        Chat Interface
```

### V1 must have

* [ ] User authentication
* [ ] Dashboard
* [ ] Plugin registry
* [ ] GitHub connection
* [ ] Google Drive connection
* [ ] Agent creation
* [ ] Agent instructions
* [ ] Plugin selection
* [ ] Tool selection
* [ ] MCP client
* [ ] Tool discovery
* [ ] Tool routing
* [ ] Tool execution
* [ ] Read/write permissions
* [ ] Human approval for write operations
* [ ] Agent chat
* [ ] Conversation history
* [ ] Tool execution timeline
* [ ] Error handling
* [ ] PostgreSQL
* [ ] Docker
* [ ] Tests

That is already a **very solid portfolio project**.

---

# 🚀 Development order

> **Each phase below now has its own document** in
> [`phases_doc/`](phases_doc/README.md), with sub-phases, traps,
> a build order and testable milestones.

| Phase | Document | Status |
|---|---|---|
| 1 — MCP server | — | Complete (61 tools) |
| 2 — Agent Engine | [Phase2.md](phases_doc/Phase2.md) | Complete (147 tests) |
| 3 — Backend | [Phase3.md](phases_doc/Phase3.md) | Next |
| 4 — Frontend | [Phase4.md](phases_doc/Phase4.md) | Planned |
| 5 — Security | [Phase5.md](phases_doc/Phase5.md) | Planned |
| 6 — Production | [Phase6.md](phases_doc/Phase6.md) | Planned |
| V2 — Platform | [V2.md](phases_doc/V2.md) | Future |
| V3 — Scale | [V3.md](phases_doc/V3.md) | Speculative |

The original ordering, unchanged:

```text
PHASE 1
Current MCP Server
├── GitHub ✅
└── Drive → finish
          ↓
PHASE 2
MCP Client / Agent Engine
├── tool discovery
├── tool routing
└── execution
          ↓
PHASE 3
Backend
├── auth
├── users
├── agents
├── plugins
└── conversations
          ↓
PHASE 4
Frontend
├── dashboard
├── plugins
├── agent builder
└── chat
          ↓
PHASE 5
Security
├── permissions
├── OAuth
└── approvals
          ↓
PHASE 6
Production
├── PostgreSQL
├── Redis
├── Docker
├── tests
└── CI/CD
          ↓
V2
├── Plugin marketplace
├── Custom MCP servers
├── Memory
├── Workflows
└── Scheduling
          ↓
V3
├── Multi-agent
├── Observability
├── Plugin SDK
└── Community ecosystem
```

**The key point:** don't start by building the marketplace, multi-agent system, workflows, or 15 integrations. Build the **smallest complete Agent Hub around the GitHub + Drive MCP servers you already have**, make that architecture clean, and then expand it. That will let you learn fast while still ending up with something that can genuinely grow into a production SaaS.

---

## Three things to know before Phase 3

All three come from the code as it exists today, and all three are
expensive to discover late. Each is covered in full in the phase that
solves it.

**1. The MCP session cannot be per-request.**
`ai_client.py` spawns `python server.py` as a subprocess. One
subprocess per HTTP request does not work.
→ [Phase3.md](phases_doc/Phase3.md)

**2. The MCP server is single-tenant.**
`os.getenv("GITHUB_TOKEN")` means every Agent Hub user would share one
GitHub account.
→ [Phase5.md](phases_doc/Phase5.md)

**3. Approval cannot block.**
`ConsoleApproval` calls `input()`. A web request cannot wait ten
minutes for a human, so the agent turn must become suspendable.
→ [Phase3.md](phases_doc/Phase3.md) design,
[Phase5.md](phases_doc/Phase5.md) implementation
