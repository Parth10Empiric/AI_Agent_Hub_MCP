# Understanding the Tool Expansion and the Bug Fixes

> **Who this is for:** you in six months, or anyone who opens commit
> `0eb669e` ("Bug fix and Expand tools") and asks *why is this diff
> 15,000 lines?*
>
> This document covers one commit: what grew, what broke, why it broke,
> and what each fix teaches. Every bug here was found in a **real
> session**, and the transcripts are quoted so you can recognise the
> shape of the problem next time.
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
13. [Testing](#13-testing)
14. [Operating it](#14-operating-it)
15. [The patterns that repeat](#15-the-patterns-that-repeat)
16. [Known gaps](#16-known-gaps)
17. [Glossary](#17-glossary)

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

Test suite: **519 passing.**

> **One thing here is not in that commit.** [Part J](#part-j--permissions-for-services-you-have-not-connected)
> — hiding permissions for services you have not connected — was found
> and fixed afterwards and is still uncommitted at the time of writing:
>
> ```
>  M api/schemas/permission.py
>  M api/services/permission_service.py
>  M frontend/app/(app)/agents/[id]/permissions/page.tsx
>  ?? tests/permissions/test_scope_visibility.py
> ```
>
> It belongs with the rest of the story, so it is documented here.

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

## 13. Testing

**519 tests pass.** The test files this work added:

| File | Covers |
|---|---|
| `tests/router/test_task_routing.py` | task expansions, typo intent, per-clause intent, read floor, chainable args |
| `tests/router/test_semantic_gating.py` | when embeddings run, fail-soft, honest warm counting |
| `tests/agent/test_find_tools.py` | mid-turn discovery, bounds, the grounding check |
| `tests/context/test_data_freshness.py` | stale labels, ages, refresh detection, credential invalidation |
| `tests/permissions/test_blocked_capabilities.py` | the withheld-tool notice |
| `tests/permissions/test_scope_visibility.py` | connected flag, orphaned grants *(Part J — uncommitted)* |
| `tests/permissions/test_tool_visibility.py` | scope-driven tool availability |
| `tests/plugins/test_credential_verification.py` | every service, every failure mode |
| `tests/plugins/test_connect_requires_verification.py` | nothing is written on rejection |
| `tests/catalogue/test_tool_sync.py` | agents pick up tools that shipped later |
| `tests/surface/test_tool_surface.py` | the whole 161-tool surface |
| `tests/services/test_github_contents.py` | the file/directory shape bug |

```bash
.venv/bin/python -m pytest tests -q         # backend
cd frontend && npx tsc --noEmit             # frontend types
cd frontend && npx next lint                # frontend lint
```

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
- **A partial embedding failure at startup** leaves some tools without
  vectors until a later query fills them in lazily. Harmless, but the
  startup log reports the smaller number — which is the honest thing to
  do, and worth not "fixing".

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
