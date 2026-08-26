# Understanding the Tool Expansion and the Bug Fixes

> **Who this is for:** you in six months, or anyone who opens commit
> `0eb669e` ("Bug fix and Expand tools") and asks *why is this diff
> 15,000 lines?*
>
> It covers that commit and everything that followed from it: what
> grew, what broke, why it broke, and what each fix teaches. Every bug
> here was found in a **real session**, and the transcripts are quoted
> so you can recognise the shape of the problem next time.
>
> Read [what came after the commit](#what-came-after-the-commit) first
> if you only want the recent work.
>
> Every technical word is explained the first time it appears. The
> language is kept simple on purpose.

---

## Table of contents

1. [The commit at a glance](#1-the-commit-at-a-glance)
2. [Part A — Expanding the tools (61 → 161)](#part-a--expanding-the-tools-61--161)
3. [Part B — Permissions became the only gate](#part-b--permissions-became-the-only-gate)
4. [Part C — Classification caught up with the new tools](#part-c--classification-caught-up-with-the-new-tools)
5. [Part D — Errors that tell you how to fix them](#part-d--errors-that-tell-you-how-to-fix-them)
6. [Part E — The router was blind](#part-e--the-router-was-blind)
7. [Part F — The model can now ask for tools](#part-f--the-model-can-now-ask-for-tools)
8. [Part G — Stale data and invented answers](#part-g--stale-data-and-invented-answers)
9. [Part H — A permission system that says no out loud](#part-h--a-permission-system-that-says-no-out-loud)
10. [Part I — A token is not connected until the service says so](#part-i--a-token-is-not-connected-until-the-service-says-so)
11. [Part J — Permissions for services you have not connected](#part-j--permissions-for-services-you-have-not-connected)
12. [Part K — Multi-tenancy: the .env fallback](#part-k--multi-tenancy-the-env-fallback)
13. [Part L — The service an agent was never given](#part-l--the-service-an-agent-was-never-given)
14. [Part M — Three reasons a tool is missing, three screens](#part-m--three-reasons-a-tool-is-missing-three-screens)
15. [Part N — Forbidding the answer, not just the excuse](#part-n--forbidding-the-answer-not-just-the-excuse)
16. [Part O — "Yes, delete it" routed to Google Calendar](#part-o--yes-delete-it-routed-to-google-calendar)
17. [Part P — Drive pagination told the truth badly](#part-p--drive-pagination-told-the-truth-badly)
18. [Part Q — The turn that erased its own record](#part-q--the-turn-that-erased-its-own-record)
19. [Part R — Failures the user could not see](#part-r--failures-the-user-could-not-see)
20. [Part S — A chat that opens at the end and stays there](#part-s--a-chat-that-opens-at-the-end-and-stays-there)
21. [Part T — Proof that it is still working](#part-t--proof-that-it-is-still-working)
22. [Part U — The transcript shows what worked](#part-u--the-transcript-shows-what-worked)
23. [Testing](#13-testing)
24. [Operating it](#14-operating-it)
25. [The patterns that repeat](#15-the-patterns-that-repeat)
26. [Known gaps](#16-known-gaps)
27. [Glossary](#17-glossary)

---

## 1. The commit at a glance

```
commit 0eb669e  "Bug fix and Expand tools"
89 files changed, 15,430 insertions(+), 1,299 deletions(-)
```

Two things happened at once, and they are related:

**The tool catalogue nearly tripled.**

| Service | Before | After |
|---|---:|---:|
| GitHub | 18 | **65** |
| Slack | 14 | **43** |
| Google Calendar | 16 | **28** |
| Google Drive | 13 | **25** |
| **Total** | **61** | **161** |

**And that broke things that had quietly been working.**

This is the important lesson of the whole commit, so it goes first:

> Most of these bugs were not *introduced* by the expansion. They were
> **revealed** by it. A router that picks 8 tools out of 61 is choosing
> from a small, distinctive set. The same router picking 8 out of 161 —
> where eleven tools contain the word "repo" — is choosing from noise.
> Scale does not create bugs so much as remove the luck that was hiding
> them.

Test suite: **547 passing.**

### What came after the commit

Parts A–I are `0eb669e`. The rest were found by **using the product**
after it shipped, and each one is here because a real session went
wrong:

| Part | Landed in | What it fixes |
|---|---|---|
| **J** | `b28e694` | permissions listed for services you never connected |
| **L** | *uncommitted* | an agent could never be given a service after creation |
| **M** | *uncommitted* | "switched off" pointed at a screen with no switch |
| **N** | *uncommitted* | the agent invented Drive folders rather than refuse |
| **O** | *uncommitted* | "Yes, delete it" routed to Google Calendar |
| **P** | *uncommitted* | Drive's `total` meant "in this page", and read as "in your Drive" |
| **Q** | *uncommitted* | a turn created 16 GitHub issues and recorded none of them |
| **R** | *uncommitted* | five ways a failure reached the user as silence |
| **S** | *uncommitted* | the chat loaded every message, and opened at the oldest |
| **T** | *uncommitted* | a four-minute turn looked frozen after ninety seconds |
| **U** | *uncommitted* | old transcripts full of failed attempts nobody needs |

Parts L–P share one root, and it is worth naming before you read them:

> Every one of them is a system telling the user something **true about
> its own state** and **false about the world**. "Switched off" was true
> of a flag and false about the screen. `total: 25` was true of a page
> and false about the Drive. The agent's "I made those tools up" was
> true of the current turn and false about the conversation. Correct
> internals are not the same as honest output.

Parts Q–U are the same idea one layer out. Everything up to here was
about the AGENT telling the truth; these are about the PRODUCT doing it
— a failed turn that leaves no trace, an error rendered next to a
spinner, a chat that opens at its first message. The backend can be
perfectly correct and the screen can still be lying.

---

## Part A — Expanding the tools (61 → 161)

### What was added

- **GitHub (18 → 65):** files and directories, branches, commits and
  comparisons, pull requests and reviews, releases, workflows and
  workflow runs, collaborators, organisations, starring, search across
  repositories / issues / code / users.
- **Slack (14 → 43):** rebuilt from scratch — messages (send,
  ephemeral, scheduled, update, delete), channel management, history
  and replies, reactions, pins, files, users and user groups,
  conversations, search.
- **Google Calendar (16 → 28):** recurring events, move/import,
  free-busy, ACL rules, colours, settings.
- **Google Drive (13 → 25):** read and export file content, revisions,
  copies, permissions, trash operations, shared drives.

### The rename that matters

```
services/Slack/  →  services/slack/
```

Not cosmetic. The **namespace** — the short service name like `github`
or `slack` — is used as one identifier across the entire system:

```
ToolDefinition.namespace  →  "slack"
plugin_connections.plugin_key  →  "slack"
agent_scopes.scope  →  "slack:message:write"
OAuth provider registry key  →  "slack"
```

If the folder is `Slack` and everything else says `slack`, the pieces
stop lining up on a case-sensitive filesystem — which Linux is and
Windows is not. That is the kind of bug that only appears on the
deployment machine.

### The one API bug worth knowing

`github_get_file` used to crash when given a directory:

```python
data = await github.get_contents(...)
return data.get("path")     # AttributeError: 'list' has no attribute 'get'
```

GitHub returns an **object** for a file and an **array** for a
directory. The crash was classified as a `service_error`, which reads
as retryable, so the agent retried it three times. And there was no
other way to list a directory, so the model kept trying `path="/"` —
it was doing the right thing with a tool that could not do it.

The fix ([services/github/tools.py](../services/github/tools.py))
handles both shapes and returns a `"type"` field so the model knows
which it got:

```python
if isinstance(data, list):
    return {"type": "dir", "entries": [...]}
return {"type": "file", "content": ...}
```

**Lesson:** when a tool can return two shapes, say which one it
returned. The model cannot inspect Python types — it only reads JSON.

---

## Part B — Permissions became the only gate

### The bug

You ticked GitHub write tools in the agent's settings, granted the
write permission, and the agent still could not write.

### Why

There were **two** gates, and both had to pass:

```
agent_tools.enabled    the per-tool checkbox     ← a preference
agent_scopes.scope     the permission grant      ← the envelope
```

`enabled` defaulted to `tool.read_only`, so every write tool was
created with `enabled = false`. Granting the scope flipped one gate;
the other stayed shut, invisibly. The executor ANDs them:

```python
if tool.name not in self._enabled:  deny     # gate 1
if not granted & tool.permissions:  deny     # gate 2
```

### The fix — one gate, not two

The per-tool checkbox grid was **deleted**
(`frontend/components/agents/tool-picker.tsx`), and a migration opened
every existing row:

```sql
-- alembic/versions/b7c41a92e5d3_enable_all_agent_tools.py
UPDATE agent_tools SET enabled = true WHERE enabled = false;
```

That is safe precisely **because `enabled` was never the security
boundary** — `agent_scopes` is. An agent seeded with read scopes still
cannot write after this migration. It just stops being vetoed twice,
once visibly and once invisibly.

### And the UI now says what will happen

`GET /api/agents/{id}` now returns, per tool:

- `permitted` — would a granted scope cover this?
- `required_scopes` — which grants would unblock it?

Both **computed on the server**, using the same policy object the
executor uses.

> **Why that matters:** if the browser re-derived "is this covered?"
> from a scope list, there would be two implementations of one security
> rule — and two implementations of a security rule drift. The one that
> is right becomes a coin toss.

---

## Part C — Classification caught up with the new tools

**Classification** is how the system decides, at discovery time, what a
tool *does*: `READ`, `WRITE`, `DELETE` or `ADMIN`. It works from the
verb at the start of the tool's name.

100 new tools brought verbs the table had never seen:

```python
READ_VERBS   += "compare", "diff"
WRITE_VERBS  += "star", "join", "leave", "invite", "pin", "react",
                "copy", "restore", "import", "respond", "share",
                "publish", "open"
DELETE_VERBS += "unstar", "unpin", "trash", "empty", "kick"
```

Note the `un-` verbs. **Reversing a write is a delete** — something
that existed stops existing.

### The ADMIN correction

Four new tools were classified as ordinary writes and should not have
been:

```python
"github_add_collaborator":     Operation.ADMIN
"github_remove_collaborator":  Operation.ADMIN
"slack_add_channel_members":   Operation.ADMIN
"slack_remove_channel_member": Operation.ADMIN
```

`ADMIN` exists for one reason: what changes is **not your data, but who
else can reach it**. Keeping these separate is what lets a user grant
*"this agent may write to GitHub"* without also granting *"this agent
may hand my private repositories to strangers."*

---

## Part D — Errors that tell you how to fix them

### The service's own words were being thrown away

GitHub refused a search with a complete set of instructions:

```
"Query must include 'is:issue' or 'is:pull-request'"
```

That sentence was parsed, stored on the execution record — and never
shown to the model. The agent saw only our category message
("GitHub rejected the request because the supplied data is invalid")
and had no way to fix the call.

`ToolError.detail` now carries it, capped at 300 characters so a
service that returns a wall of text cannot spend the whole context
budget.

### Three approval outcomes, not one

```
APPROVAL_DENIED       a person read the arguments and said no
APPROVAL_EXPIRED      they were asked and never answered
APPROVAL_UNAVAILABLE  there was nobody to ask (a plain POST, a CLI)
```

All three end the same way — the call did not run — but merging them
tells people things that are not true. Someone who clicked **Deny** was
told "there was nobody to answer"; someone whose scheduled job stalled
was told they refused something they never saw.

### And the approval list stopped lying

`approvals_required` used to be appended to when a call was *requested*,
not when it was *refused*. So every **approved** call was reported as
one the agent "needed permission for", and the chat rendered
*"Nothing was changed in your accounts"* directly underneath a tool
that had just successfully run.

---

## Part E — The router was blind

This is the biggest fix in the commit. Read this part even if you skip
the rest.

### The transcript

```
User:  "Student-Faculty-ApplicationReview-System repo summury provide me"

Agent: "This summary is based on the repository metadata... I can only
        fetch README and file contents if I have a tool to do so — and
        the tools available to me right now don't include a generic
        'fetch file by path' call."
```

The agent then guessed the tech stack from the repository's **name**.

### What the router actually did

```
github_list_pull_requests   0.501  lex=0.275 ns=1.0 op=0.5 exec=0.74 sem=0.0
github_list_repositories    0.466  lex=0.188 ns=1.0 op=0.5 exec=1.00 sem=0.0
github_get_repository       0.449  lex=0.188 ns=1.0 op=0.5 exec=0.74 sem=0.0
github_create_repository    0.449  lex=0.188 ...
github_delete_repository    0.449  lex=0.188 ...   ← on a READ request
...eleven candidates inside 0.06
github_get_readme           0.438  ← rank 12, cut by top_k = 8
```

**Every scoring signal failed at once:**

| Signal | Value | Why |
|---|---|---|
| `sem` | 0.0 everywhere | embeddings were never switched on in production |
| `op` | 0.5 everywhere | "summury" is not a verb, so no intent was detected |
| `lex` | 0.188 on eleven tools | they all contain "repo" and nothing else matched |
| — | — | the repository **name** contributed nothing at all |

What actually decided the ranking was `executability` — a 0.06-weight
tiebreaker meaning *"how few arguments does this need?"*. So the model
was handed eight ways to **manage** repositories and no way to **read**
one.

And the router reported **confidence 0.70**.

### The nine fixes

**1. Task expansions** ([agent/lexicon.py](../agent/lexicon.py))

Some words name a **plan**, not a tool. Nothing is called `summarize`;
no docstring contains the word. But a summary is *made of* a readme.
That knowledge lives in a person's head, so it gets written down:

```python
TASK_EXPANSIONS = {
    "summary":  ("readme", "file", "content", "description", "history"),
    "stack":    ("readme", "file", "content", "language"),
    "structure":("file", "content", "tree", "directory", "folder"),
    ...
}
```

Three properties make this safe to keep extending: it **cannot invent a
term** (every expansion is looked up in the index vocabulary, so a
stale entry rots into a no-op), it **cannot shout over the user**
(weighted at 0.6 of a typed word), and it **cannot double-count** (a
term the user typed themselves is skipped).

**2. Fuzzy intent verbs** ([agent/router.py](../agent/router.py))

Intent detection was the one exact-match test left in an otherwise
typo-tolerant router. `"summury"` produced no intent, so the read-only
filter never ran. Now typos are forgiven — guarded by **length** (both
words ≥ 5 characters) rather than by a high threshold, because that is
where verb collisions actually come from.

**3. Silence is not consent**

```python
if intent is None:
    if operation in (DELETE, ADMIN):
        return UNASKED_DESTRUCTIVE_ALIGNMENT   # 0.15, not 0.5
```

A flat neutral 0.5 is how `github_delete_repository` came to be offered
alongside seven other repository tools for a request that only wanted
to look at something.

**4. Per-clause intent**

`"list my repos and delete the stale branch"` has two intents. One
sentence-level intent has to pick one and be wrong about the other. The
router now splits on connectives and maps *service → intent*.

**5. A guaranteed read tool per service**

Clause splitting cannot fix `"post a slack summary of my github
issues"` — one clause, one verb, two services. So a floor stands behind
it: **every selected service keeps at least one way to look at
something.** Reads are the universal prerequisite; a service with no
read offered can only be written to blind.

**6. Chainable arguments**

```
github_get_file(owner, repo, PATH)      path comes from a listing
google_drive_read_file(FILE_ID)         id comes from a search
slack_get_message(channel_id, TS)       ts comes from a history
```

Nobody types a path, a file id or a timestamp. Counting them as
"missing information" penalised every tool that can only run **second**
— so multi-step tasks were scored worst on exactly the tools that would
complete them.

**7. Confidence measures separation, not magnitude**

Old: `0.4 × best_service + 0.6 × best_tool_score` — which answers *"did
anything score well?"* when the question is *"did anything **win**?"*
A field bunched inside 0.06 is a coin toss wearing a certificate.

**8. Proper nouns stopped routing requests**
([agent/text.py](../agent/text.py))

`"review" in "applicationreview"` scored **0.90** — all but an exact
match — lifting every pull-request-review tool to the top of a request
about summarising a repository. Containment now requires the shorter
word to be at least half of the longer one.

**9. The fallback stopped throwing away its own ranking**

When confidence is low the router widens. It used to return *the first
20 tools in declaration order* — which is why `github_get_file` appeared
(declared early) and `github_get_readme` did not (declared later). Low
confidence means *"I am not sure which of these wins"*, **not** *"these
scores are meaningless"*. It now widens the ranking and tops up from
the index.

### The result

```
0.520 github_get_file
0.516 github_get_readme
0.510 github_list_repositories
...
intent = READ, no destructive tools, no fallback
```

### Semantics, switched on — as a rescue signal

**Embeddings** turn text into a list of numbers positioned so that
similar meanings get similar numbers. They were wired in during Phase
2.3 and never enabled, so `semantic` read `0.0000` in every production
score breakdown.

They are now on ([api/main.py](../api/main.py)) — but **not on every
message**. Measured on the development machine:

```
embed one query              ~120 ms
everything else routing does  ~15 ms
```

So the rule is: **run the cheap signal first, measure whether it
worked, and pay for the expensive one only when it did not.**

```python
lexical_inconclusive = max(lexical_scores) < LEXICAL_DECISIVE   # 0.55
semantic = self._semantic_scores(query, pool) if lexical_inconclusive else {}
```

```
"list my github issues"            best 1.00 → keywords worked →  20 ms
"send a message to the channel"    best 0.85 → keywords worked →  22 ms
"what is this project built with"  best 0.45 → nothing matched → 133 ms
```

A first attempt used a **tie test** instead, and it was wrong. Both of
these are ties:

```
1.00, 1.00, 0.87, 0.87, 0.87   six issue tools agreeing — correct
0.27, 0.19, 0.19, 0.19, 0.19   eleven unrelated tools sharing "repo"
```

What separates them is the **height** of the cluster, not its width.

Startup embeds all 161 tools in batches of 32 (~11 s total), off the
request path. A provider that fails on its **first** call is treated as
misconfigured and disabled permanently; one that fails after working is
forgiven three times. Same exception, different meaning, told apart by
*when* it happened.

---

## Part F — The model can now ask for tools

Routing happens **once**, before the turn, from the user's sentence.
That is fine when the whole plan is in the words. It cannot work when
step 2 depends on step 1:

```
"summarise the Student-Faculty-ApplicationReview-System repo"

  step 1  list the repository root      ← implied by the words
  step 2  read the files that turn up   ← WHICH files?
```

Nobody can name the tools for step 2 before step 1 has run, because the
**arguments do not exist yet**.

So the loop now offers a meta-tool, `find_tools`
([agent/loop.py](../agent/loop.py)):

```
model → find_tools("read a file from a github repository")
      → router runs again, with a query written by something that has
        now SEEN the data
      → the tools are added to THIS turn, callable immediately
```

Before this, `ollama_tools` was built once before the loop and frozen
for all 50 rounds — a model that worked out what it needed in round 2
could never be given it.

**What it deliberately is not:** an escape hatch from permissions. The
loop intercepts it *above* the executor, so it never reaches the
permission policy, the risk classifier or the MCP server. The tools it
returns come from the same callback carrying the same policy filter as
the first routing pass.

> It widens what the model can **see**. Nothing widens what it can
> **do**.

Bounded: 4 searches per turn, identical searches refused, and every
"nothing found" reply is written to *end* the line of enquiry rather
than invite another.

---

## Part G — Stale data and invented answers

### The transcript

```
turn 1  "list out all repo names"
        → github_list_repositories CALLED. 1 repo. Correct.

turn 2  "again try to call tool and send me current data not old"
        → NO tool call. "You currently have 2 repositories..."

turn 3  "I changed my token, tell me the new repo list"
        → NO tool call. "3 repos — and test-repo is new since you
           changed your token!"
```

Turns 2 and 3 were **generated, not fetched**. The router was not at
fault: that turn was offered 20 tools including
`github_list_repositories`.

### Two mechanisms, two fixes

**Reading beat calling.** A complete repository list sat in context
with **nothing to say when it was true**. Calling a tool costs a round;
re-reading costs nothing. The model took the cheap path, which was the
rational one given what it could see.

So every tool result now carries a timestamp, and replayed ones are
marked ([agent/untrusted.py](../agent/untrusted.py)):

```
[tool_result tool="github_list_repositories" trust="stale-snapshot"
             captured="2026-08-25T09:12:03+00:00" age="18m" id="9f2a"]
```

**Agreeing beat checking.** *"I changed my token"* asserts that more
repositories exist. With no data to consult, the most probable
continuation is one that satisfies the assertion — so the model
invented exactly one, gave it a plausible name and marked it private.

Hence `DATA_FRESHNESS` in the system prompt, which forbids **both**
halves: never restate an earlier answer as fresh, and *never add,
remove or adjust items to match what the user seems to expect.*

### But the prompt is the weak half

Two enforcement layers sit under it, and neither asks the model's
permission ([api/context.py](../api/context.py)):

**1. Refresh detection deletes the payloads.**

```python
wants_fresh_data("again try to call tool ... not old")  → True
build_llm_messages(..., drop_tool_results=True)
```

A rule the model must *choose* to follow is weaker than a context that
offers nothing to copy.

**2. Credential changes invalidate everything older.**

```python
invalid_before = await _credentials_changed_at(session, user_id)
```

A GitHub token swapped for a different account's makes every earlier
GitHub result an answer about **somebody else** — and the conversation
contains no hint of that, because reconnecting happens on the settings
page, not in the chat. The timestamp is the only thing that can tell
those results apart.

### The grounding check

Found later, in a different conversation:

```
"list out all github repo name"
  rounds = 1, ZERO tool calls
  → a formatted table of ten repositories with names and links
```

Assembled from the model's own earlier replies further up the
conversation, and it looked **more convincing than the honest answer**
that preceded it.

So the loop now checks: an answer containing a **markdown table**,
produced with **zero successful tool calls**, is sent back once with an
instruction to fetch it.

Why a table? It is the narrowest reliable signal of *"I am asserting
facts"* in a model's output. Everything fabricated in these transcripts
arrived as a table — because **prose hedges; tables assert**. Matching
numbered lists instead would fire on every *"1. Direct message
2. Specific channel"* menu, which reports nothing.

It fires **once**. If the model insists, its answer stands. This nudges;
it does not argue.

---

## Part H — A permission system that says no out loud

### The transcript

```
User:  "list out all github repo name"

Agent: "I don't have a tool available that can list all GitHub
        repositories... My available GitHub tools can only operate on
        repositories that are already named (github_list_workflow_runs,
        github_star_repository, github_update_repository,
        github_create_repository)."
```

Every word true from where the model was standing — and completely
misleading.

### What the database said

```json
{"services": ["github"], "tool_count": 6, "confidence": 0.8489,
 "fallback_used": false, "tool_searches": 2, "rounds": 3}
```

Six tools, where the budget was eight. The router had ranked
`github_list_repositories` **third**. The agent's scopes were:

```
github:*:admin
github:authenticated_user:read
github:repository:write
github:workflow_run:read
```

`github_list_repositories` requires `github:repository:read` or
`github:*:read`. **Neither was granted** — so the tool was filtered out
before the model ever saw it, and `find_tools` (which applies the same
filter) correctly found nothing, twice.

```
the truth            "you have not granted me repository reads"
what the user heard  "this product cannot list repositories"
```

### The fix

The turn now routes a **second** time over the tools that *failed* the
policy, keeps the ones that outscore the weakest tool actually offered,
and tells the model ([api/services/chat_service.py](../api/services/chat_service.py)):

```
[runtime notice] These tools match this request but were withheld from
you: github_list_repositories (needs the permission
github:repository:read or github:*:read)... Do not tell the user the
capability does not exist. If you cannot answer without them, say
plainly which permission is missing.
```

Relevance is not a new heuristic — it is *"this would have been in your
toolset if it were permitted"*, measured by the same ranking that chose
the others.

> **The rule this restores:** a permission system must be able to say
> **no out loud**. Silently removing a capability and letting the model
> improvise the reason is the worst of both worlds — the user is not
> told what to fix, and they are told something false about the
> product.

---

## Part I — A token is not connected until the service says so

### The bug

Paste `hello-world-1234` into the GitHub token box → **201 Created** →
green **Connected** badge.

`plugin_service.connect()` encrypted whatever string arrived, wrote the
row, set `status = "connected"` and never contacted GitHub. The word
"connected" was describing **our database**, not any connection.

Two other fields were taken straight from the request body:

```python
connection.account_label = account_label   # whatever the browser sent
connection.scopes = list(scopes or [])     # whatever the browser sent
```

So a connection could sit in the list labelled `finance@company.com`
while holding somebody's personal token. **A label is a claim about an
account; only the account can make it.**

### The fix — [api/verification.py](../api/verification.py)

Each service is checked by **calling it the way the agent will**:

| Plugin | Endpoint |
|---|---|
| `github` | `GET /user` |
| `slack` | `POST /api/auth.test` |
| `google_drive` | `GET /drive/v3/about` |
| `google_calendar` | `GET /calendar/v3/users/me/calendarList` |

Not a regex on `ghp_` — that passes a revoked token and fails every
fine-grained PAT GitHub ships next year. And deliberately **not** a
generic token-introspection endpoint: a Google token scoped only for
Drive is *rejected* by userinfo and works perfectly for Drive. The
question is not *"is this token valid somewhere"* but *"can it do what
this plugin needs"*.

### Three outcomes, three status codes

```
201  verified, stored, genuinely connected
422  the service said no    → your token is wrong, here is what to check
503  we could not ask       → our problem, try again in a minute
```

That third one is where this kind of fix usually goes wrong. Called a
rejection, it tells someone their good token is bad. Called a success,
the original lie is back with a rationalisation attached.

Two traps handled:

- **GitHub answers 403 for both** "your token is wrong" and "you asked
  too often" — split by the `x-ratelimit-remaining` header.
- **Slack answers HTTP 200 with `{"ok": false}`** — a status-code check
  alone passes every invalid token Slack has ever issued.

**Nothing is written unless verification passes.** The tests assert on
the **database**, not on the exception — a verify-then-store-anyway
implementation passes every test that only checks the raise. And
reconnecting with a typo now leaves your working connection intact,
instead of turning a typo into an outage.

### Re-checking what was already stored

Connecting proves a credential worked **once**. Tokens get revoked,
expire, or are rotated elsewhere — and none of that notifies us.

`POST /api/plugins/{key}/verify` re-tests a stored credential, with a
**Test** button on each service card. It returns **200 even when the
token is dead**, and that is not sloppiness:

> Recording the answer *writes* (`status` becomes `"revoked"`), and
> `get_db` rolls the transaction back whenever an endpoint raises. A
> 4xx would report the dead token and then **discard the row that
> recorded it**.

The UI now has **three** states — Connected / **Needs reconnecting** /
Not connected — because a connection whose credential stopped working
is not the same as never having connected one.

---

## Part J — Permissions for services you have not connected

The permissions page listed every scope of every service, whether or
not you had connected it. Granting `google_drive:*:read` with no Google
account achieves nothing: the tool is offered, called, and fails at the
credential resolver several screens later.

That is how a security screen becomes a wall of switches — and a wall
is what people click through without reading, on the one page where
reading is the entire point.

`ScopeOption` now carries `connected`, and the page splits accordingly:

| Situation | What you see |
|---|---|
| Service connected | Full section of switches |
| Not connected, no grants | One line in a "Not connected" list, with **Connect** |
| Not connected, **has grants** | Full section + amber `not connected` badge |

**Marked, not filtered** — and the third row is why:

> Connect GitHub → grant `github:*:write` → disconnect GitHub. The
> grant is still recorded and applies again the moment the service is
> reconnected. If the server dropped that scope, the UI would have no
> row to revoke it. **A permission you cannot see is a permission you
> cannot take away.**

---

## Part K — Multi-tenancy: the .env fallback

`core/tenancy.py` decides whose credentials a tool call uses. The old
rule was per **deployment**:

```python
if token is None and not env_credentials_allowed():
    raise MissingCredential(...)
```

In development, `env_credentials_allowed()` is true — so a **user's**
request with no token silently fell back to the operator's `.env`
tokens. That is the exact cross-tenant leak Phase 5.5 exists to close,
and it would look, to that user, like the feature working.

The rule is now per **request**:

- No user id at all → the CLI, a script, a test → `.env` is correct.
- A user id but no token → that person has not connected this service,
  or their token could not be decrypted → **refuse**.

The backend sets `user_id` on every call it makes on somebody's behalf,
so the distinction is reliable.

---

## Part L — The service an agent was never given

### The bug

Connect Google Drive. Grant `google_drive:*:read` on the permissions
page — it saves, the switch goes green. Ask the agent about Drive:

> *"Google Drive is switched off in this agent's tool settings."*

There was no such switch. The per-tool checkbox grid had been removed
in [Part B](#part-b--permissions-became-the-only-gate).

### Why

An agent only ever sees tools from a **namespace it holds rows for** in
`agent_tools`. Those rows are written **once**, by the create wizard,
and nothing added a namespace afterwards:

```
create_agent       writes rows for the services you ticked
sync_agent_tools   adds new TOOLS — but only for namespaces the agent
                   ALREADY has, deliberately, so connecting a service
                   does not silently widen every existing agent
grant_scope        never touched agent_tools at all
```

So gate 2 (the scope) passed and gate 1 (the row) had nothing to pass.
The permission was real, and it was a permission over **nothing at
all**.

### The fix — the missing lever

`PUT /api/agents/{id}/services` and a **Services** section on the agent
settings page ([services-section.tsx](../frontend/components/agents/services-section.tsx)).

The granularity is the point: people think in *"add Google Drive to
this agent"*, not in 25 individual Drive tools.

**The whole set, not a delta.** The screen behind it is a list of ticks
and one Save button — a PUT is the only shape that cannot apply half of
what someone chose.

**Adding seeds READ scopes and never write.** Same rule as
`create_agent`, for the same reason: adding Drive to an agent is
consent for it to *look* at Drive. It is not consent to delete files.

**Removing revokes that service's scopes too** — not tidiness,
correctness. The permissions page now lists only the services an agent
has, so a grant left behind for a removed service would be live and
invisible at once. Each revocation gets its own audit row.

**Idempotent.** A PUT that changes nothing writes nothing — no audit
row, no bumped `updated_at`. A UI that re-saves an unchanged form must
not produce a history that reads like the user kept changing their mind.

### Two questions that look like one

`AgentSummary` now carries both, because conflating them *was* the bug:

```
namespaces   services with at least one ENABLED tool row
services     services this agent has ANY row for
```

And the permissions page filters by the second — with the same
exception as [Part J](#part-j--permissions-for-services-you-have-not-connected):

> A scope the agent **already holds** stays listed whatever else is
> true of it. That is how it gets revoked. `ScopeOption.on_agent` marks
> the case so the UI can say *why* a switch is present but useless.

### The mental model, now three layers

```
Services (settings)     which services does it HAVE?    the surface
Permissions             what may it DO with them?       the envelope
Connections (plugins)   is there an account behind it?  the credential
```

All three must be true. Each has its own screen, and the failure
message now names the right one — which is [Part M](#part-m--three-reasons-a-tool-is-missing-three-screens).

---

## Part M — Three reasons a tool is missing, three screens

The withheld-tool notice from [Part H](#part-h--a-permission-system-that-says-no-out-loud)
knew **two** reasons a tool could be absent. There are three, and the
one it did not know was the one that had just been reported:

| Reason | What the user must do | Which screen |
|---|---|---|
| Not on this agent | add the service | Settings → Services |
| Switched off | enable the tool | (API only, no grid) |
| Not permitted | grant the scope | Permissions |

Reporting the third case as the second is what sent someone to a
settings page to flip a switch that was not on it
([chat_service.py](../api/services/chat_service.py)):

```python
agent_services = {definition.namespace for name in enabled ...}

if (candidate.tool.namespace or "") not in agent_services:
    # No permission grant fixes this. The fix is one screen back.
```

**The lesson:** an error message that names the wrong remedy is worse
than one that names none. "Something is off" makes people look;
"switched off in your tool settings" makes them look *in the wrong
place*, and then conclude the product is broken.

---

## Part N — Forbidding the answer, not just the excuse

### The session

Every Google Drive tool blocked, and the notice from Part H in place:

```
user   "so you just listout all folder name"
agent  five folder names, five Drive-shaped ids, creation and
       modification dates, a total. All invented.

user   "you need to call tool, not guess"
agent  "Success! I called the actual Google Drive tool" — it had
       called two Slack tools, and repeated the same fabrication.
```

### Why the notice did not stop it

Because **nothing in it was disobeyed.** The first version constrained
the *wording of a refusal* — do not say the capability does not exist —
and left the model free to do something worse instead. It never said
*"do not make the data up"*, because that seemed too obvious to write
down.

It is not obvious:

> A model with a direct request, no tool, and a user insisting has one
> completion available that satisfies everybody, and it is the false
> one. **Refusing is the improbable continuation, so it has to be the
> instructed one.**

### The fix

`BLOCKED_NOTICE` now forbids the answer, and names the shapes that were
actually fabricated:

> *You have **NO DATA** of the kind these tools return: do not produce
> it from memory, from earlier in this conversation, or from what the
> request implies it should look like. **Names, identifiers, counts,
> dates and lists** invented to fill this gap are indistinguishable from
> real ones to the user, and **stating that you called a tool you did
> not call is worse again.** Answer with what is missing and how to
> grant it, and nothing else.*

Naming the shapes matters. A blanket *"be accurate"* is what the model
already believed it was being.

And the prompt is still the cheap half. The load-bearing half is that
those tools are absent from `mcp_tools`, absent from `allowed_tools`,
and refused by the executor if asked for anyway.

---

## Part O — "Yes, delete it" routed to Google Calendar

### The session

```
user   "list out all github repo naem"
agent  lists two real repositories. github_list_repositories called,
       592 ms, succeeded.

user   "delete test repo"
agent  explains the deletion is permanent, asks for confirmation.

user   "Yes, delete it"
agent  "I don't actually have access to GitHub tools. Looking back at
        our conversation, I realize I made up those tools."
```

It had not made them up. It had called one, two messages earlier.

### What actually happened

```
"yes"  →  "yesterday"    0.94
```

`yesterday` is a Google Calendar alias, and `fuzzy_ratio` scores a
three-letter prefix at **0.94** — all but an exact match. So the
message scored `google_calendar` at 0.940 and scored nothing else.
Every GitHub tool was filtered out before the model saw the turn.

**And the carry-over did not save it** — that is the second half of the
bug. `route` carries the previous turn's services forward only when the
current message names **no** service. This message named one.
Confidently. The wrong one.

So the model was handed Calendar tools, a transcript full of GitHub
work, and a request to delete a repository. **Disowning its own history
was the only reading that made all three consistent.**

### Two fixes, at two levels

**1. At source — the affirmatives are stopwords**
([agent/lexicon.py](../agent/lexicon.py)):

```python
"yes", "yeah", "yep", "yup", "yah", "ya",
"sure", "agreed", "absolutely", "definitely", "affirmative",
```

A confirmation **carries no topic by definition** — it inherits the
topic of the question it answers. Note `"no"` was already on that list
and `"yes"` was not.

The rule this list must never break: none of these is an intent verb.

**2. As a backstop — `TOOL_AUTHORITY` now covers a SHRINKING toolset**
([api/context.py](../api/context.py)):

> *The set also **shrinks**. A tool result already in this conversation
> was really fetched: if the tool that produced it is absent now, say
> it is unavailable for this message — never that you imagined the tool
> or made the result up.*

The original rule only covered a toolset that **grew** (Phase 5.8: the
model trusting its own stale refusals). Routing narrows the toolset
every turn, on purpose, and will do it again for honest reasons.

> *"I cannot do that right now"* is recoverable. *"I lied to you
> earlier"* is not — it teaches the user to distrust the results that
> **were** true.

---

## Part P — Drive pagination told the truth badly

`google_drive_list_files` returned:

```json
{"files": [...], "total": 25}
```

`total` was named when it was accurate — before the tool could
paginate, the page *was* everything. Once pagination arrived the name
stopped matching the number, and nothing announced the change. To
anything that had not read the source — model or person — `total: 25`
reads as *"you have 25 files in your Drive"*.

Two fixes ([services/google_drive/](../services/google_drive/)):

**The field says what it counts.**

```json
{"files": [...], "returned": 25, "total": 25,
 "next_page_token": "abc...", "has_more": true}
```

`returned` cannot be misread. `total` stays as an alias so stored
results and older callers keep working — it is the same value it always
was.

**`has_more` is a boolean, deliberately.** It is the same fact as
`next_page_token`, in the form a model reliably acts on: `"next_page_token": "abc"`
requires knowing what a page token is; `"has_more": true` does not.

And the underlying API bug: the Drive request asked for `files(...)` as
its field mask, which **silently drops `nextPageToken`** — which is why
this looked like an API with no pagination at all.

**Lesson:** a field name is documentation that ships to the caller. When
the thing it counts changes, the name has to change with it — and the
old name only survives as an alias if the *value* did not change too.

---

## Part Q — The turn that erased its own record

### The session

```
05:53:31  user  "Yes, create the issues"
05:53:31  approval requested   github_create_issue
   ...    22 approvals granted, one at a time, over three minutes
05:56:52  approval granted     github_create_issue
          — and then nothing, ever

tool_executions rows for that turn:  0
assistant message:                   none
issues actually on GitHub:           16
```

The chat showed a question the agent appeared to ignore.

### Why the work survived and the record did not

Everything a turn does is spread across three places that fail
**differently**:

| | Behaviour on failure |
|---|---|
| **GitHub** | not transactional at all — an issue created is created |
| **`audit_log`** | commits as it goes, because an approval must be visible to the request that answers it |
| **the turn itself** | **one transaction**, committed at the very end — user message, every execution, the answer |

So a failure before that final commit discards **precisely the record of
the work that happened**, while keeping every trace of having asked
permission to do it. The user's own message survived only by accident,
carried along by an approval's commit.

### The fix

Nothing can make a turn transactional once a real GitHub issue exists.
This makes the **record** honest instead
([chat_service.py](../api/services/chat_service.py),
[stream.py](../api/routers/stream.py)):

- every `tool_end` payload is kept **outside** the turn's transaction —
  the `ExecutionRecord` objects die with the turn, but the stream has
  already been handed a copy of each one;
- on failure a **fresh session** writes an assistant message plus the
  executions that really ran. It must be a new session: the turn's own
  transaction is already rolled back, which is the situation this exists
  to survive.

**Honest limit:** this catches exceptions. It does **not** catch the
process being killed — if the server is restarted mid-turn, `except`
never runs and the record is still lost. The next step, if it recurs, is
writing each execution as it completes rather than at the end.

### The follow-up: a note that was false

The first version wrote its note for *any* failed turn. That shipped,
and the model provider immediately started answering 429. Every turn
failed before its first tool call, so every turn — including the first
message of a brand new chat — was answered with:

> *"This turn stopped before it finished… work that was already done
> cannot be undone by the failure."*

about work that never started. Two notes now, because the difference is
the only part the reader needs:

```
TURN_FAILED_NOTE    work happened and was not recorded → check the service
TURN_STOPPED_NOTE   nothing ran → "nothing was changed in any of your accounts"
```

And a third correction, from the database:

```
06:37:02  assistant  "This turn stopped before it..."
06:37:20  assistant  "This turn stopped before it..."
06:37:32  assistant  "This turn stopped before it..."
```

**Three explanations and not one question.** `send_message` adds the
user's message first, with a comment saying it is *"saved BEFORE
anything can fail"* — and one transaction around the whole turn defeats
that intent completely. The recovery path now puts the question back
when it is missing, and only when it is missing: a turn that died after
an approval committed still has its own, and a second copy would be
worse than none.

---

## Part R — Failures the user could not see

Five bugs, one shape: **something failed, and the screen did not say
so.** They are grouped because fixing any one alone would have left the
user in the same place.

### 1. The model provider's refusal arrived as "The agent turn failed"

```
ResponseError status=429
  "you (parthp) have reached your weekly usage limit,
   upgrade for higher limits: https://ollama.com/upgrade"
```

Every one of those is fixable by the person reading it — 429 a quota,
401 an expired `ollama signin`, 400 a model that cannot do what was
asked — and every one arrived as a generic failure, which is fixable by
nobody.

`ResponseError` is now caught specifically and the provider's own
sentence is passed through, behind a lead that says **whose** problem it
is ([stream.py](../api/routers/stream.py)). Without that lead, *"you
have reached your weekly usage limit"* reads as a limit inside this
product. A 429 is reported with `code: "rate_limited"`, so the UI shows
it in the calm amber style — **a quota is not a fault**.

> Same rule as `ToolError.detail` in [Part D](#part-d--errors-that-tell-you-how-to-fix-them):
> the service's own sentence is the actionable part.

### 2. The composer silently refused every later message

```js
// the chat page decides whether to enable the composer
const busy = live !== null && live.phase !== "idle";

// the hook decides whether to actually send
if (!content.trim() || live) return;
```

Two rules, disagreeing about exactly one state: a finished-or-failed
turn still on screen. The page says *not busy* → composer enabled, Send
button live. The hook says *live is set* → returns immediately.

No request, no error, no feedback. Reported as *"the chat below my last
message disappears"*, and the only cure was a page reload.

### 3. The error was wiped microseconds after it rendered

```js
await refreshHistory();
setLive(null);          // ← including the turn carrying the error
```

**A failed turn is not a failed request.** The response is a 200
`text/event-stream`; the server reports the problem as an *event* and
closes the stream **normally**. So the success path ran. A turn carrying
an error now stays on screen.

### 4. The streaming call never refreshed its token

Every request in the app goes through `api()`, which catches a 401,
refreshes once and retries — using a **single shared in-flight promise**,
because the backend rotates refresh tokens on every use and two
concurrent refreshes invalidate each other.

`streamTurn` talks to `fetch` directly — it has to, because it reads a
stream and `api()` parses JSON — and in doing so **opted out of session
renewal entirely**. Access tokens last 15 minutes, so a chat left open
for a quarter of an hour answered the next message with *"Invalid or
expired token"* while every other panel on the page kept working.

It now refreshes through the same shared promise. Retrying is safe
precisely because a 401 means the request was **rejected** — it never
reached `chat_service`, so no turn ran and nothing is sent twice.

### 5. "Thinking… 24s" underneath the reason it stopped

The live turn rendered a two-way choice: an answer, or the status line.
A failed turn has **neither** — so it fell to the `else` and showed a
working indicator beside its own error, with the clock still counting,
because the status line's timer does not care why it is mounted.

Nothing was running. **"Thinking" next to the reason it stopped is the
single most misleading thing that screen can say.**

### And one ordering bug found by a test

A new test asserted `["user", "assistant"]` and got `["assistant",
"user"]`:

```
2026-08-26 05:32:10.373557  assistant  "Hmm, my friend..."
2026-08-26 05:32:10.373557  user       "list out all repo name"
```

Identical to the microsecond. PostgreSQL's `now()` is the **transaction
start** time, and a turn writes the question and the answer in one
transaction — so a reply can sort **above** the question it answers. The
chat UI compensates by tie-breaking on role, which works and hides the
fact that the stored data cannot say which came first; anything else
reading in order gets it wrong half the time, silently.

Migration `c4e19f7a1d02` switches the column to `clock_timestamp()`,
which reads the wall clock at the moment of the INSERT. Still stamped by
the **database** — the reason `server_default` was chosen over a Python
default — so two app servers with drifting clocks still cannot disagree.

---

## Part S — A chat that opens at the end and stays there

### The problem

`loadHistory` fetched **50 messages every time**, and a conversation
that has run for a month has thousands of rows. The cost is paid on
*opening* the conversation — the moment the user is least willing to
wait and least able to understand why.

The server was already right: `list_messages` does keyset pagination
with a row-value cursor and `limit + 1` to answer *"is there more?"*
without a second `COUNT(*)`. The frontend was ignoring it.

```
page 1: items=25  has_more=True   cursor=eyJ0IjoiMjAyNi0wOC...
page 2: items=5   has_more=False
overlap between pages: 0     total distinct: 30
```

Zero overlap matters: that conversation contains the identical-timestamp
rows from Part R, and `(created_at, id) < (t, i)` handles the tie. Plain
`limit`/`offset` would have duplicated or dropped rows.

### What was built

| | |
|---|---|
| **Page size 25** | opens instantly; most conversations never page at all |
| **IntersectionObserver** on a top sentinel | fires once on becoming visible; cannot miss a fast flick the way a scroll threshold can |
| **400px `rootMargin`** | the next page is usually there before the reader reaches the end of what they have |
| **Scroll-position restore** | `useLayoutEffect` measures `scrollHeight` either side and scrolls down by the height added — same frame as the paint, so there is no visible jump |
| **Dedupe by id** | a turn landing between two fetches can legitimately appear in both pages |
| **`loadingRef`, not state** | two scroll events fire before React re-renders; both would read the same stale `false` |
| **"Load earlier messages" button** | scroll-triggered loading is invisible to keyboard navigation |

A post-turn refresh **merges** rather than replaces, and leaves the
cursor alone — otherwise every turn threw away the older pages the
reader had scrolled up to load.

### Then it opened at the oldest message

Shipping that produced a worse bug than the one it fixed. The list
rendered at `scrollTop 0` — the top — and the *smooth* scroll to the
bottom was still animating when the paging observer, watching a sentinel
sitting right there at the top, decided the reader wanted older
messages. It fetched a page. Then another. **The conversation opened at
its first message and walked backwards through history.**

Three fixes: anchor **instantly** rather than smoothly (where the screen
starts is not a movement to watch), keep paging **switched off until
anchored** (state, not a ref, so the observer effect re-runs), and
re-anchor on conversation switch after clearing the list.

### Then following stopped working

The auto-scroll was gated on *"is the scrollbar near the bottom?"*,
measured in a `scroll` listener. That reading is unreliable for two
reasons that compound: a `scroll` event fires for **our own** scrolling
too, and the content keeps growing while the animation runs, so each
frame aims at a bottom that has already moved. One reading of "far from
the bottom" and following switched off for the rest of the turn.

> **Following is an INTENT, not a measurement.**
>
> It is turned **off** only by a user gesture — `wheel`, `touchmove`, a
> scrolling key. A programmatic scroll cannot produce any of those, so
> it can never switch itself off.
>
> It is turned **on** by position alone, from any scroll: arriving back
> at the bottom, however you got there, means "follow again".
>
> A rule that can only be disabled deliberately cannot be disabled by
> accident.

And it follows everything now, not just whole messages: a
**ResizeObserver** on the list, because height changes for reasons no
dependency array can enumerate — markdown finishing layout, a tool row
expanding, the status line growing by a word. The old effect listed four
dependencies and missed all of them.

The keyboard listener is on `window`: the scroll container is a plain
div with no `tabindex`, so a listener on it would be dead code and
someone reading back with Page Up would be dragged to the bottom by the
next token.

---

## Part T — Proof that it is still working

### The complaint

A turn that runs for four minutes showed the same sentence for four
minutes, beside a dot that pulsed once a second. **A pulse that repeats
looks identical whether the work is progressing or the process has
died** — so the honest reading of the screen, after ninety seconds of
"Thinking…", was that it had crashed. Users reload a page that was about
to answer them.

### The information already existed

`round_start` and `tokens` arrived on the SSE stream every round and had
**no case in `applyEvent`**. They were being dropped on the floor.

```
● ● ●  Reading github repositories   1m 04s · step 7 · 4.2k tokens
```

Three things, in order of how much they matter:

- **The clock ticks on its own** — a `setInterval` in the component, not
  driven by the server. It is the only element that moves during the
  long silence between a question and the model's first token, which is
  exactly the stretch that looked dead.
- **The activity names the thing** — `Reading github_get_file`. Slow is
  fine when you can see *what* is slow. The verb comes from the tool's
  own first word, the same convention `classification.py` reads it by,
  so a tool added tomorrow describes itself with no table to update.
- **Round and token counters** advance on their own schedule, proving
  the loop is turning even when the activity has not changed.

Three bounce dots on staggered delays rather than one pulse — a
travelling wave cannot be mistaken for a frozen frame. `tabular-nums` so
the clock does not shuffle the line sideways. And the clock is **hidden
while waiting for approval**: there, time passing is the *user's* doing,
and a rising counter reads as the system struggling rather than as a
question nobody has answered.

---

## Part U — The transcript shows what worked

While a turn runs, a failed call is the agent **visibly trying
something**, and the next row shows what it did about it. That is
progress, and the live timeline is left exactly as it was.

Afterwards, the same rows are archaeology. The answer above already
accounts for whatever went wrong, and a transcript full of red rows
reads as a broken product rather than as an agent that recovered. One
turn in these transcripts made four `create_issue` attempts that were
refused before the fifth succeeded; all five sat in the history for
ever, and the four said nothing the answer did not.

`SettledTimeline` renders stored messages;`ToolTimeline` still renders
the live turn.

**Two things are deliberately not hidden:**

- **`denied` rows stay.** A refusal is not a failure — it is the
  permission system working, and it needs an action from the user.
- **A small "N failed attempts" toggle remains.** Hidden by default, one
  click to see, never gone. When an answer looks wrong the first
  question is what the agent tried, and silently deleting the evidence
  would cost more than the clutter did.

The duration in the header now sums only the rows on screen: a total
that counts rows the reader cannot see is a number they cannot check.

---

## 13. Testing

**547 tests pass.** The test files this work added:

| File | Covers |
|---|---|
| `tests/router/test_task_routing.py` | task expansions, typo intent, per-clause intent, read floor, chainable args |
| `tests/router/test_semantic_gating.py` | when embeddings run, fail-soft, honest warm counting |
| `tests/agent/test_find_tools.py` | mid-turn discovery, bounds, the grounding check |
| `tests/context/test_data_freshness.py` | stale labels, ages, refresh detection, credential invalidation |
| `tests/permissions/test_blocked_capabilities.py` | the withheld-tool notice |
| `tests/permissions/test_scope_visibility.py` | connected flag, orphaned grants *(Part J)* |
| `tests/catalogue/test_agent_services.py` | adding/removing a service, seeded reads, revoked scopes *(Part L)* |
| `tests/router/test_confirmations.py` | "yes" must not change the subject *(Part O)* |
| `tests/permissions/test_tool_visibility.py` | scope-driven tool availability |
| `tests/plugins/test_credential_verification.py` | every service, every failure mode |
| `tests/plugins/test_connect_requires_verification.py` | nothing is written on rejection |
| `tests/catalogue/test_tool_sync.py` | agents pick up tools that shipped later |
| `tests/surface/test_tool_surface.py` | the whole 161-tool surface |
| `tests/services/test_github_contents.py` | the file/directory shape bug |
| `tests/chat/test_failed_turn_recording.py` | a dead turn keeps its record, puts the question back, and does not warn about work it never did *(Part Q)* |

```bash
.venv/bin/python -m pytest tests -q         # backend
cd frontend && npx tsc --noEmit             # frontend types
cd frontend && npx next lint                # frontend lint
```

> **Do not run `next build` while `next dev` is running.** They share
> `.next/` and write completely different things into it, so a build
> silently replaces the chunks the dev server is still reading:
>
> ```
> Cannot find module './vendor-chunks/mdast-util-to-markdown.js'
> MODULE_NOT_FOUND at .next/server/app/api/.../stream/route.js
> ```
>
> Neither is a bug in the code - both mean "the file this process
> expected is gone". The cure is `rm -rf .next` and a dev-server
> restart. `tsc --noEmit` and `next lint` verify everything these
> changes needed and touch nothing.

Database-backed tests open a connection, run inside a transaction and
**roll back** — nothing lands in your development database. If
PostgreSQL is unreachable they report a skip rather than failing for a
reason unrelated to the code.

---

## 14. Operating it

### Semantic routing

```bash
ollama pull nomic-embed-text
```

Settings ([api/settings.py](../api/settings.py)):

```python
embeddings_enabled: bool = True
embedding_model: str = "nomic-embed-text"
```

Confirm at startup:

```
INFO | api.main | semantic routing: 161 tools embedded
INFO | api.main | MCP ready: 161 tools across 4 services
```

`unavailable, using keyword routing only` means the model is not pulled
or Ollama is unreachable — **and the agent still works**, with keyword
routing. That fail-soft path is deliberate: an optional ranking signal
must never be a startup dependency.

### Diagnosing a bad turn

The routing record persisted on every assistant message is the audit
trail:

```sql
select created_at, content, routing from messages order by created_at desc limit 10;
```

Read it in this order:

| Field | What it tells you |
|---|---|
| `tool_count` well under budget | the **permission filter** bit, not the router |
| `fallback_used: true` | the router was not confident |
| `tool_searches > 0` | the model went looking for more tools |
| `blocked_tools` | which capabilities were withheld, and why |
| `refresh_forced` / `stale_results_dropped` | why it re-fetched, or forgot |

Then compare the agent's scopes against the tool's `permissions` tuple.

---

## 15. The patterns that repeat

1. **Scale reveals, it does not create.** Three of these bugs existed at
   61 tools and were invisible until 161.

2. **A signal that is identical across every candidate decides
   nothing** — no matter how heavily you weight it.

3. **Run the cheap check first; pay for the expensive one only where
   the cheap one failed.** The expensive signal is not *better*, it is
   better *at something else*.

4. **Never let a system silently remove a capability.** Withhold it and
   *say so*, or the model invents the reason and the user debugs the
   wrong thing.

5. **A prompt is advice; the context is enforcement.** Asking the model
   not to reuse stale data works most of the time. Deleting the stale
   data works every time.

6. **Distinguish "no" from "I could not ask."** It appears three times
   in this commit — approvals, credential verification, and the
   embedding provider — and collapsing it is wrong in both directions
   every time.

7. **Uncertainty must never return fewer options than confidence.**

8. **Two implementations of one rule will drift.** Compute permission
   answers once, on the server, with the object that enforces them.

9. **Correct internals are not honest output.** "Switched off" was true
   of a flag and false about the screen. `total: 25` was true of a page
   and false about the Drive. Both were working exactly as written.

10. **An error that names the wrong remedy is worse than one that names
    none.** "Something is off" makes people look. "Switched off in your
    tool settings" makes them look in the wrong place, then conclude the
    product is broken.

11. **Constrain the answer, not the excuse.** Forbidding one wrong
    sentence leaves every other wrong sentence available — and the model
    will find the one that satisfies everybody, which is the false one.

12. **A field name is documentation that ships.** When what it counts
    changes, the name changes with it.

13. **Work and the record of work fail differently.** GitHub is not
    transactional, the audit log commits as it goes, the turn commits at
    the end - so the one thing a rollback destroys is the evidence of
    what really happened.

14. **A failed turn is not a failed request.** The error arrives inside
    a perfectly successful 200 stream, and every "did it work?" check
    written against the HTTP status gets it backwards.

15. **State an intent, do not measure it.** "Is the scrollbar near the
    bottom?" was measured, and our own scrolling answered it. "Has the
    user scrolled away?" is an intent, and only a gesture can set it.

16. **A number that changes is the difference between working and
    hung** - and it is the only difference a user can see.

17. **One definition per question.** The page and the hook each decided
    what "busy" meant; the disagreement dropped messages in silence.

---

## 16. Known gaps

- **A single clause naming two services** still cannot have its intent
  split (`"post a slack summary of my github issues"`). Mitigated by
  the guaranteed read tool per service, not solved.
- **`write` does not imply `read`** in the scope model, and nothing
  warns you when you grant one without the other. That is what broke
  the Jtest agent. Deliberately not changed silently — widening a
  permission rule is a decision, not a bug fix.
- **Nothing marks a connection dead** when a tool call fails with an
  auth error mid-turn. The plugins page still says Connected until
  someone presses **Test**.
- **`ConnectRequest.scopes`** is now ignored by the server and should
  come out of the API contract.
- **The per-tool `enabled` flag has no screen.** It survives as an API
  lever and as gate 1 of the policy, but since the checkbox grid was
  removed nothing in the UI can flip it — which is why the "switched
  off" branch of [Part M](#part-m--three-reasons-a-tool-is-missing-three-screens)
  can only ever be reached through the API.
- **Only Drive's listing paginates honestly** ([Part P](#part-p--drive-pagination-told-the-truth-badly)).
  The same `total`-that-means-page pattern is worth auditing in the
  GitHub and Slack list tools before it is reported rather than after.
- **A partial embedding failure at startup** leaves some tools without
  vectors until a later query fills them in lazily. Harmless, but the
  startup log reports the smaller number — which is the honest thing to
  do, and worth not "fixing".
- **A killed process still loses the turn.** [Part Q](#part-q--the-turn-that-erased-its-own-record)
  recovers from exceptions, not from `SIGKILL` — `except` never runs.
  The fix, if it recurs, is writing each execution as it completes
  rather than at the end of the turn.
- **A batch action asks for approval once per call.** Sixteen issues
  meant **22 separate prompts**. The permission model is right; the
  ergonomics are not. An "approve all `github_create_issue` for this
  turn" option is a design decision, not a bug fix.
- **The 15-minute access token is now refreshed on the stream too**, but
  a turn that runs *longer* than the token's life still holds one
  request open across the expiry. It works — the header was validated at
  the start — and is worth knowing before someone shortens the lifetime.
- **`google_calendar_create_event` rejects times with no timezone**
  (`"Missing time zone definition for start time"`). Diagnosed, not yet
  fixed - the tool should default to the calendar's own timezone rather
  than pass a naive timestamp through.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Tool** | One callable action on a service, e.g. `github_get_file` |
| **Namespace** | The short service name — `github`, `slack` — used as one identifier across the registry, connections, scopes and OAuth |
| **Scope** | A permission sentence: `service:resource:action`, e.g. `github:issue:write` |
| **Routing** | Choosing which handful of tools to show the model for one message |
| **Lexical score** | Keyword match strength between the query and a tool's name, description and keywords |
| **IDF** | Inverse document frequency — how rare a word is across all tools; rare words count for more |
| **Embedding** | Text turned into a list of numbers positioned so similar meanings land near each other |
| **Cosine similarity** | The angle between two embeddings — 1.0 identical, 0.0 unrelated |
| **Intent** | Whether a message asks to read, write, delete or administer |
| **top_k** | How many tools the router is allowed to offer this turn |
| **Fallback** | What routing returns when it is not confident: *more* tools, not fewer |
| **Policy** | The object that answers "may this agent use this tool?" — enabled AND scoped |
| **Framing** | Wrapping tool output in a delimited block so the model can tell data from instructions |
| **Stale snapshot** | A tool result fetched in an earlier turn and replayed into this one |
| **Verification** | Asking a service whether a credential actually works, before claiming it does |
