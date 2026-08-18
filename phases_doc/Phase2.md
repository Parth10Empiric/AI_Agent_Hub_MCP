# Phase 2 — MCP Client / Agent Engine

> **Status:** COMPLETE
> **Delivered:** dynamic discovery, tool classification, hybrid
> typo-tolerant routing, safe execution with retries, full telemetry
> **Tests:** 147 passing (`python tests/run_tests.py`)
> **Walkthrough:** [`../devloper_docs/Understand_Phse2.md`](../devloper_docs/Understand_Phse2.md)
>
> The plan below is preserved as written. What actually shipped -
> including where reality differed from the plan - is documented in
> [What actually shipped](#what-actually-shipped) at the end.

---

Yes — **you can move to Phase 2 now.** In fact, with **GitHub + Google Drive + Slack + Google Calendar and 61 tools**, Phase 2 is exactly the right next step.

But there is one important change from your earlier prototype:

> **Do not send all 61 tools directly to the LLM and let it choose from them every time.**

Now that you have 61 tools, this is the point where you should build a proper **MCP Client / Agent Engine** with discovery, filtering/routing, execution, and error handling.

## Phase 2 target architecture

Your current system:

```text
MCP Servers
│
├── GitHub
├── Google Drive
├── Slack
└── Google Calendar
        │
        ▼
   61 MCP Tools
```

Phase 2 turns this into:

```text
                    Agent Engine
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      Discovery       Router         Executor
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                   MCP Client
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
    GitHub             Drive             Slack
                                             │
                                         Calendar
```

---

# Phase 2.1 — MCP Tool Discovery

First, your Agent Engine needs to know what tools are available.

You already have 61 tools, so create a **Tool Registry**.

For example:

```python
ToolDefinition(
    name="github_create_issue",
    server="github",
    description="Create a new GitHub issue",
    category="github",
    read_only=False,
)
```

Another:

```python
ToolDefinition(
    name="drive_search_files",
    server="google_drive",
    description="Search files in Google Drive",
    category="drive",
    read_only=True,
)
```

And:

```python
ToolDefinition(
    name="slack_search_messages",
    server="slack",
    description="Search Slack messages",
    category="slack",
    read_only=True,
)
```

The registry becomes:

```text
Tool Registry
│
├── GitHub
│   ├── github_search_repositories
│   ├── github_get_repository
│   ├── github_create_issue
│   └── ...
│
├── Drive
│   ├── drive_search_files
│   ├── drive_get_file
│   └── ...
│
├── Slack
│   ├── slack_search_messages
│   ├── slack_send_message
│   └── ...
│
└── Calendar
    ├── calendar_list_events
    ├── calendar_create_event
    └── ...
```

### Important

The registry should be **dynamic**.

Don't manually maintain 61 tool definitions if the MCP server can expose them.

Your MCP client should connect → call tool discovery → populate the registry.

---

# Phase 2.2 — Tool normalization

This is something I strongly recommend before routing.

Different MCP servers might expose tools with different naming/description styles.

Normalize them into one internal structure:

```text
Tool
├── id
├── name
├── description
├── server
├── category
├── input_schema
├── read_only
├── risk_level
└── permissions
```

Example:

```json
{
  "name": "github_create_issue",
  "server": "github",
  "category": "github",
  "description": "Create a GitHub issue",
  "read_only": false,
  "risk_level": "medium"
}
```

This gives your Agent Engine a **common interface** regardless of which plugin provides the tool.

---

# Phase 2.3 — Tool routing ⭐

This is the most important part for your project.

Suppose the user says:

> "Find my open GitHub issues."

Don't send all 61 tools to the LLM.

Instead:

```text
User request
     │
     ▼
Tool Router
     │
     ▼
Relevant service
     │
     ▼
GitHub tools
     │
     ├── github_list_issues
     ├── github_search_issues
     └── github_get_issue
```

Then give only those tools to the LLM.

---

# Your router should work in layers

I recommend:

```text
User Request
      │
      ▼
Intent / Service Detection
      │
      ▼
Tool Candidate Filtering
      │
      ▼
Tool Ranking
      │
      ▼
Top-K Tools
      │
      ▼
LLM
```

For example:

### User:

> "Send a Slack message to John."

Router:

```text
Service:
Slack

Candidates:
slack_search_users
slack_send_message
slack_get_channel
```

Not:

```text
61 tools
```

---

# Don't rely only on keywords

This is particularly important because you've previously had problems with spelling mistakes and keyword filtering.

Don't build:

```python
if "github" in query:
    ...
```

as your main routing system.

Instead use a **hybrid router**.

```text
                User Query
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
    Semantic Router       Keyword Hints
          │                   │
          └─────────┬─────────┘
                    ▼
               Tool Ranking
                    │
                    ▼
                 Top-K
```

So even:

> "show me my githb issues"

can still find GitHub tools.

---

# Phase 2.4 — Tool ranking

Give each candidate a score.

For example:

```text
Tool                         Score

github_search_issues          0.94
github_list_issues            0.91
github_get_issue              0.72
drive_search_files            0.08
slack_search_messages        0.05
```

Then send the top few to the LLM.

This becomes important as you eventually have:

```text
61 tools
→ 100 tools
→ 300 tools
→ 1000+ tools
```

Your architecture won't need to change.

---

# Phase 2.5 — MCP Tool Execution

Once the LLM chooses:

```text
github_search_issues
```

your executor handles it.

```text
LLM
 │
 │ tool call
 ▼
Tool Executor
 │
 ├── Validate tool
 ├── Validate arguments
 ├── Check permissions
 ├── Execute MCP call
 ├── Handle timeout
 ├── Handle error
 └── Return result
```

Then:

```text
Tool result
     ↓
LLM
     ↓
Final response
```

---

# Phase 2.6 — Error handling

Do not ignore this.

Your executor should handle:

```text
MCP connection failure
Tool not found
Invalid arguments
Authentication failure
Permission denied
Timeout
Rate limit
API error
Malformed response
Server unavailable
```

For example:

```text
GitHub MCP
    ↓
API timeout
    ↓
Executor
    ↓
Retry
    ↓
Still failed
    ↓
Return structured error
    ↓
Agent decides what to do
```

---

# Phase 2.7 — Retry mechanism

Implement something like:

```text
Attempt 1
   ↓
Failure
   ↓
Wait
   ↓
Attempt 2
   ↓
Failure
   ↓
Attempt 3
   ↓
Return error
```

But don't retry everything.

For example:

```text
Timeout          → retry
Connection error → retry
429 rate limit   → retry with backoff
401 unauthorized → don't retry
403 forbidden    → don't retry
Invalid arguments → don't retry
```

---

# Phase 2.8 — Execution metadata

Every tool execution should produce something like:

```json
{
  "execution_id": "exec_123",
  "tool": "github_search_issues",
  "server": "github",
  "status": "success",
  "started_at": "...",
  "duration_ms": 421
}
```

This will become extremely useful later for your Agent Hub UI.

You'll be able to show:

```text
✓ github_search_issues       421ms
✓ drive_search_files         583ms
✓ slack_search_messages      312ms
```

---

# Phase 2.9 — Agent loop

Now combine everything.

Your first proper Agent Engine loop should look like:

```text
User
 │
 ▼
Agent
 │
 ▼
Router
 │
 ▼
Relevant tools
 │
 ▼
LLM
 │
 ├── Final answer?
 │       │
 │       └── YES → Response
 │
 └── Tool call?
         │
         ▼
      Executor
         │
         ▼
      MCP Server
         │
         ▼
       Result
         │
         ▼
        LLM
         │
         └─────── repeat if necessary
```

For example:

> "Find the latest GitHub issue about authentication and tell me if we discussed it in Slack."

The agent might execute:

```text
1. github_search_issues
        ↓
2. github_get_issue
        ↓
3. slack_search_messages
        ↓
4. Final answer
```

That's a **real multi-tool agent workflow**.

---

# One thing I would NOT build yet

Don't add:

```text
❌ Multi-agent
❌ Marketplace
❌ Workflow builder
❌ Long-term memory
❌ Plugin SDK
❌ 20 more services
❌ Complex vector database
```

right now.

You already have **61 tools**.

That's more than enough for Phase 2.

Your job now is to make those 61 tools **usable intelligently**.

---

# Recommended Phase 2 folder structure

I would evolve your current project toward something like:

```text
agent-hub/
│
├── mcp_servers/
│   ├── github/
│   ├── drive/
│   ├── slack/
│   └── calendar/
│
├── agent/
│   ├── client.py
│   ├── engine.py
│   ├── router.py
│   ├── planner.py
│   ├── registry.py
│   ├── executor.py
│   ├── schemas.py
│   └── errors.py
│
├── tests/
│   ├── mcp/
│   ├── router/
│   ├── executor/
│   └── agent/
│
├── config/
│
└── server.py
```

You don't have to restructure everything immediately; this is the **target architecture**.

---

# Phase 2 milestone

I would consider Phase 2 complete when this works:

### Test 1

> "List my open GitHub issues."

```text
Router
→ GitHub
→ github_list_issues
→ result
→ answer
```

### Test 2

> "Find my MCP documents in Drive."

```text
Router
→ Drive
→ drive_search_files
→ result
→ answer
```

### Test 3

> "Find messages in Slack about the GitHub deployment."

```text
Router
→ Slack
→ slack_search_messages
→ result
→ answer
```

### Test 4

> "What meetings do I have tomorrow?"

```text
Router
→ Calendar
→ calendar_list_events
→ result
→ answer
```

### Test 5 — most important

> "Find the GitHub authentication issue and check whether anyone discussed it in Slack."

```text
                User
                  │
                  ▼
               Router
                  │
          ┌───────┴────────┐
          ▼                ▼
       GitHub             Slack
          │                │
    Search issue     Search messages
          │                │
          └───────┬────────┘
                  ▼
                 LLM
                  │
                  ▼
              Final answer
```

If that works reliably, **Phase 2 is genuinely successful**.

---

## 🎯 Your next immediate task

Since Phase 1 is now:

**GitHub + Drive + Slack + Calendar = 61 tools ✅**

don't add anything else.

Start Phase 2 with:

```text
1. MCP Client
      ↓
2. Dynamic Tool Discovery
      ↓
3. Tool Registry
      ↓
4. Tool Normalization
      ↓
5. Hybrid Tool Router
      ↓
6. Tool Ranking
      ↓
7. Tool Executor
      ↓
8. Error + Retry handling
      ↓
9. Agent Loop
      ↓
10. Test multi-tool tasks
```

And **keep your current 61-tool MCP implementation as the plugin/service layer**. The new Agent Engine should consume it through MCP rather than reaching directly into GitHub/Drive/Slack/Calendar APIs.

That separation is exactly what will make the eventual **Agent Hub website** clean: later, the frontend doesn't care whether a tool comes from GitHub, Drive, Slack, Calendar, or a third-party MCP server—it simply talks to the Agent Engine.

---
---

# What actually shipped

*Written after Phase 2 was completed. The plan above was mostly right;
this section records where it was wrong, incomplete, or where reality
demanded more.*

## Delivered modules

```text
agent/
├── schemas.py          ToolDefinition, Operation, RiskLevel
├── classification.py   what a tool does + how dangerous     (2.2)
├── discovery.py        MCP -> normalized + classified       (2.1, 2.2)
├── registry.py         thread-safe store + version counter  (2.1)
├── text.py             typo matching, stemming, edit distance
├── lexicon.py          domain vocabulary (DATA, not logic)
├── index.py            inverted index + IDF                 (2.3)
├── embeddings.py       semantic seam (off by default)       (2.3)
├── router.py           hybrid router                        (2.3, 2.4)
├── routing.py          routing result types
├── errors.py           16 error codes + retry policy        (2.6, 2.7)
├── execution.py        ExecutionRecord + redaction          (2.8)
├── permissions.py      PermissionPolicy + ApprovalHandler
├── executor.py         8-step execution pipeline            (2.5)
├── loop.py             agent loop                           (2.9)
└── engine.py           facade over all of it
```

`planner.py` was in the planned structure and is **not needed** —
routing plus the agent loop covers what a planner would have done.

## Where the plan was wrong

### `slack_search_messages` does not exist

Milestone Test 3 assumes it. Your Slack service has no message-search
tool. The router correctly falls back to `slack_channel_history`, but
that needs a `channel_id`, so the agent must call `slack_list_channels`
first. **Still worth adding a real search tool.**

### Drive cannot revoke access

`create_permission` and `list_permissions` exist; there is no delete.
*"Remove Bob's access"* routes sensibly but cannot be fulfilled.

### Drive has no zero-argument tool

Every one of its 13 tools requires at least one argument. GitHub has
`get_authenticated_user`, Slack has `auth_info`, Calendar has
`list_calendars` — Drive has nothing, so *"test drive connection"* has
no clean answer. Adding `google_drive_about` (Drive API `about.get`)
would fix it.

### Phase 2.4 was not a separate phase

Ranking is inseparable from routing. Building 2.3 delivered 2.4.

## Where the plan was incomplete

### The retry table was missing the important case

Phase 2.7 above says *timeout → retry*. That is wrong for writes:

```text
github_create_issue times out after 30s
   the RESPONSE was lost
   the REQUEST may well have succeeded
   retrying creates a SECOND issue
```

Shipped instead — **two tiers**:

```text
ALWAYS RETRYABLE     proves the request never ran
  connection_error, server_unavailable, rate_limited

AMBIGUOUS            might have taken effect -> READS ONLY
  timeout, service_error, malformed_response

EVERYTHING ELSE      never retried, including UNKNOWN
```

### Failure has three disguises, not one

The plan treats errors as exceptions. Your MCP tools catch their own
exceptions and **return a dictionary**, so MCP reports success:

```text
1. an exception was raised
2. isError is set on the result
3. the result looks fine but the payload says success: false  ← yours
```

An executor checking only 1 and 2 records a broken integration as
100% healthy.

### Four services, four error shapes

```text
github     error = {"type", "message", "status_code", "details"}
calendar   error = {"type", "message"}
slack      error = "a plain string"
drive      error = "a plain string" + a separate "code" field
```

`classify_payload()` normalizes all four.

## What was added beyond the plan

Every item below came from a **real failure**, not from theory.

| Addition | The failure that caused it |
|---|---|
| **Executability signal** | *"check ... calander connection"* picked `freebusy`, which needs 3 arguments nobody supplied |
| **Intent-verb down-weighting** | *"check"* appears in 1 of 61 docstrings, so IDF treated a command verb as decisive |
| **Adaptive tool budget** | 4 services × fixed 10 tools = 2.5 each; calendar got 1 unusable tool |
| **Per-service minimum** | a typo (*"calander"*) cost a service its reserved slots |
| **Cross-service penalty** | *"test drive connection"* ranked `slack_auth_info` above every Drive tool |
| **Argument coercion** | model sent `page_size: "1"`; a string/int mismatch failed a whole round |
| **Second-chance retrieval** | when every tool in a round fails, re-route excluding them |
| **Escalation gating** | widening the tool set cannot fix a service outage — it wasted 23s |
| **Permission + approval seams** | Phase 5 needs a decision point that already exists |
| **Argument redaction** | tokens and message bodies must never reach a telemetry table |

## Two bugs found in the server

```text
1. GoogleDriveError did not inherit MCPApplicationError, so
   handle_tool_error() flattened EVERY Drive failure to
   "Internal tool error". Fixed.

2. Drive resolved token.json RELATIVELY, against the MCP subprocess's
   working directory. The token was never found again, so an
   interactive browser OAuth flow ran on every call - a 22-second
   delay that looked like a broken integration.
   Fixed with config.settings.resolve_path().
```

## Milestone results

| Test | Result |
|---|---|
| 1. List my open GitHub issues | ✅ |
| 2. Find my MCP documents in Drive | ✅ |
| 3. Find messages in Slack | ✅ via `slack_channel_history` |
| 4. What meetings do I have tomorrow | ✅ |
| 5. GitHub issue + Slack discussion | ✅ both services get tools |

Plus, beyond the milestone:

```text
✅ typos: githb, calander, gogle drve, mesage, isues, slak
✅ synonyms with no service named: "am i free", "open tickets"
✅ read requests never surface DELETE or ADMIN tools
✅ write requests still receive lookup tools (list_users before send)
✅ routing runs in 3-9 ms on 61 tools
✅ no new dependencies added
```

## Numbers

```text
61 tools     github 18 | calendar 16 | slack 14 | drive 13
             read 38 | write 15 | delete 4 | admin 4
             safe 35 | low 3 | medium 14 | high 7 | critical 2

             classified: heuristic 53 | override 8 | default 0

147 tests    text 21 | classification 22 | routing 33 | coverage 27
             errors 22 | executor 22
```

`default 0` is enforced by a test: add a tool whose name the classifier
cannot read and the suite fails, telling you to add an override.

---

## Next

→ **[Phase3.md](Phase3.md)** — wrap this engine in FastAPI.

Read the **three hard problems** at the top of that document before
writing any Phase 3 code. All three come from the current codebase and
all three are expensive to discover late.
