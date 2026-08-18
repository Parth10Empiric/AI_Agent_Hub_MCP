# Understanding Phase 2 — The Agent Engine

> **Who this is for:** someone who has never built an AI agent before.
> By the end you should understand *what* we built, *why* every piece
> exists, *how* the algorithms work, and *how to fix it* when it
> misbehaves.
>
> No prior knowledge is assumed. Every technical word is explained the
> first time it appears.

---

## Table of contents

1. [What Phase 2 is, in plain words](#1-what-phase-2-is-in-plain-words)
2. [The big picture](#2-the-big-picture)
3. [File map — what lives where](#3-file-map--what-lives-where)
4. [Part A — Phase 2.2: Understanding our tools](#part-a--phase-22-understanding-our-tools)
5. [Part B — Phase 2.3: Choosing the right tools](#part-b--phase-23-choosing-the-right-tools)
   - [B12 Executability](#b12-executability--can-we-actually-run-it)
   - [B13 Multi-service budgets](#b13-when-the-request-touches-everything)
   - [B14 Second-chance retrieval](#b14-second-chance-retrieval)
6. [Part C — Phase 2.5–2.8: Running tools safely](#part-c--phase-2528-running-tools-safely)
7. [Following one message end to end](#7-following-one-message-end-to-end)
8. [Troubleshooting guide](#8-troubleshooting-guide)
9. [How to test and tune](#9-how-to-test-and-tune)
10. [Glossary](#10-glossary)
11. [Known gaps](#11-known-gaps)

---

## 1. What Phase 2 is, in plain words

### Where we started

After Phase 1 we had an **MCP server** with **61 tools** across four
services:

```
GitHub            18 tools
Google Calendar   16 tools
Slack             14 tools
Google Drive      13 tools
                  --------
                  61 tools
```

> **MCP** stands for *Model Context Protocol*. Think of it as a
> standard plug socket. Our server exposes "tools" (functions like
> `github_list_issues`), and any AI client can plug in and call them
> without knowing anything about GitHub's API.

The old code did the simplest possible thing: it handed **all 61
tools** to the AI model on **every single message**, then called
whatever the model asked for.

### Why that breaks

Three problems, and they get worse as you add services:

**1. It is expensive.**
Every tool has a schema (its name, description and parameters). All 61
schemas get sent to the model with every message, whether the user
asked about GitHub or the weather. You pay for those tokens every time.

**2. It is inaccurate.**
AI models get measurably worse at choosing when given more options.
Ask a person to pick the right screwdriver from a set of 5 and they
succeed. Give them a wall of 300 and they hesitate and get it wrong.
Models behave the same way.

**3. It is dangerous.**
The old code ran whatever the model asked for. No check of whether the
tool was allowed. No asking the user before deleting a file. One bad
model decision meant real, permanent data loss.

### What Phase 2 adds

```
        BEFORE                             AFTER

  user message                       user message
       |                                  |
       v                                  v
  all 61 tools  --------->          [ router ] picks ~10
       |                                  |
       v                                  v
     model                              model
       |                                  |
       v                                  v
  session.call_tool()               [ executor ]
       |                              |  validate
       v                              |  check permission
   try / except                       |  ask the human
       |                              |  run with timeout
       v                              |  classify failure
    a string                          |  retry if safe
                                      v
                                 ExecutionRecord
```

Two new brains sit between the user and the tools:

| Brain | Question it answers | Built in |
|---|---|---|
| **Router** | *Which tools does this message need?* | Phase 2.2 + 2.3 |
| **Executor** | *Is it safe to run this, and what happened?* | Phase 2.5–2.8 |

---

## 2. The big picture

```
                        USER MESSAGE
                             |
                             v
        +--------------------------------------------+
        |                AgentEngine                  |
        |            (the front door)                 |
        +--------------------------------------------+
                             |
          +------------------+------------------+
          |                                     |
          v                                     v
  +---------------+                   +-------------------+
  |   DISCOVERY   |                   |      ROUTER       |
  | runs once, at |                   | runs every message|
  | connect time  |                   |                   |
  |               |                   | 1. read the words |
  | - read tools  |                   | 2. fix typos      |
  | - classify    |                   | 3. score services |
  | - build index |                   | 4. score tools    |
  +-------+-------+                   | 5. pick top ~10   |
          |                           +---------+---------+
          v                                     |
  +---------------+                             v
  |   REGISTRY    |<--------------------  ~10 tools
  | the one place |                             |
  | tools live    |                             v
  +-------+-------+                     +-------------+
          |                             |   THE LLM   |
          |                             |  (Ollama)   |
          |                             +------+------+
          |                                    |
          |                          "call github_list_issues"
          |                                    |
          v                                    v
  +--------------------------------------------------+
  |                    EXECUTOR                       |
  |  1. does the tool exist?                          |
  |  2. was it offered this turn?                     |
  |  3. are the arguments valid?                      |
  |  4. is this agent allowed?                        |
  |  5. does a human need to say yes?                 |
  |  6. run it (with a timeout)                       |
  |  7. what kind of failure was it?                  |
  |  8. retry — only if safe                          |
  +--------------------------+-----------------------+
                             |
                             v
                     ExecutionRecord
              (what ran, how long, what broke)
```

**Read that diagram twice.** Everything below is just detail on one of
those boxes.

---

## 3. File map — what lives where

### Core engine files

| File | Size | What it does | Touch it when... |
|---|---|---|---|
| `agent/engine.py` | 250 | The front door. Owns everything else so callers wire up one object, not six. | You add a new capability to the engine |
| `agent/schemas.py` | 240 | Defines what a "tool" is to us: `ToolDefinition`, `Operation`, `RiskLevel` | You need a new field on every tool |
| `agent/registry.py` | 318 | The one place tools are stored. Thread-safe. | You need a new way to look tools up |
| `agent/discovery.py` | 268 | Reads tools from the MCP server and normalizes them | The MCP server changes its format |
| `agent/classification.py` | 382 | Decides what each tool *does* and how *dangerous* it is | A tool is classified wrongly |
| `agent/lexicon.py` | 308 | **The vocabulary file.** Words users say → services and tools | **Most of your tuning happens here** |
| `agent/text.py` | 427 | Pure text tools: typo matching, stemming, edit distance | A typo isn't being caught |
| `agent/index.py` | 318 | The fast search index over all tools | Routing gets slow |
| `agent/router.py` | 853 | Picks which tools to send to the model | Wrong tools are being chosen |
| `agent/routing.py` | 194 | The router's result types | You need more info out of routing |
| `agent/embeddings.py` | 193 | Optional AI-based meaning matching (**off by default**) | You pass 300+ tools |
| `agent/errors.py` | 514 | Every way a call can fail, as one structured type | A failure is classified wrongly |
| `agent/execution.py` | 243 | The record of what happened during one call | You need more telemetry |
| `agent/permissions.py` | 306 | Who may do what, and when to ask a human | You build Phase 5 |
| `agent/executor.py` | 676 | Runs tools safely | A tool call misbehaves |
| `agent/loop.py` | 300 | Talks to the LLM, repeats until it has an answer | You change the conversation flow |

### Test files

| File | What it proves |
|---|---|
| `tests/tool_fixtures.py` | Builds test data from your **real** server source |
| `tests/router/test_text.py` | Typos match; unrelated words don't |
| `tests/router/test_classification.py` | Every tool is classified correctly |
| `tests/router/test_router.py` | The 5 Phase 2 milestone queries work |
| `tests/executor/test_errors.py` | Failures are named correctly; retries are safe |
| `tests/executor/test_executor.py` | The 8-step pipeline works |
| `tests/run_tests.py` | Runs everything without needing pytest |

Run them all:

```bash
python tests/run_tests.py            # 120 tests
python tests/run_tests.py router     # just the router
python tests/run_tests.py executor   # just the executor
```

---

# Part A — Phase 2.2: Understanding our tools

## A1. The problem

The MCP server tells us a tool's **name** and a **one-line
description**. That's it.

```
name:        google_drive_delete_file
description: "Delete a file from Google Drive."
```

That's enough for an AI model to *call* the tool. It is not enough for
**us** to decide:

- Should we ask the user before running this?
- Is this agent allowed to run it?
- If it times out, is retrying safe?

So before anything else, we look at all 61 tools and **label** them.

## A2. Three labels per tool

```
  google_drive_delete_file
          |
          +--> operation:   DELETE     (what does it DO?)
          +--> risk_level:  CRITICAL   (how BAD if it goes wrong?)
          +--> permissions: ["google_drive:file:write",
                             "google_drive:*:write"]
```

### Label 1: `Operation` — what it does

Only four values. Fewer values = fewer branches in your code later.

| Value | Meaning | Example |
|---|---|---|
| `READ` | Looks at things. Changes nothing. | `github_list_issues` |
| `WRITE` | Creates or edits something. | `github_create_issue` |
| `DELETE` | Removes something. | `slack_delete_message` |
| `ADMIN` | Changes **who can access** something. | `google_drive_create_permission` |

> **Why is ADMIN separate from WRITE?**
> `google_drive_create_file` makes a file you own.
> `google_drive_create_permission` gives a stranger access to your file.
> Both are "create". Only one is a **data leak**. They must never be
> treated the same.

### Label 2: `RiskLevel` — how bad if it goes wrong

`SAFE → LOW → MEDIUM → HIGH → CRITICAL`

Here is the single most important lesson in this section:

> ### Risk is NOT the verb.

Look at these two. **Both are `WRITE`:**

```
google_drive_create_file    creates a file      you can delete it   -> MEDIUM
slack_send_message          sends to a human    you CANNOT unsend   -> HIGH
```

Risk measures **blast radius** (how far the damage spreads) and
**reversibility** (can you undo it?). Not grammar. Any system that
guesses risk from the verb alone will get this backwards, and it will
get it backwards on exactly the calls that matter.

### Label 3: `permissions` — who may call it

Each tool gets **two** permission strings:

```
github_create_issue
   |
   +-- "github:issue:write"   specific: "may write issues"
   +-- "github:*:write"       wildcard: "may write anything on GitHub"
```

Why both? So Phase 5 can support coarse and fine grants with **one
check** — *does the agent hold ANY of the tool's strings?*

```
Agent A granted {"github:*:read"}        -> every GitHub read, nothing else
Agent B granted {"github:issue:write"}   -> only issues, nothing else
```

Getting this shape right now is free. Adding wildcards *after* you've
stored per-tool grants in a database is a migration.

## A3. The algorithm: a ladder of evidence

To assign the labels we try four sources, **strongest evidence first**.
The moment one works, we stop.

```
   START: a tool named "github_create_issue"
     |
     v
  +--------------------------------------------------+
  | 1. OVERRIDE TABLE                                 |
  |    Did a human already inspect this exact tool?   |
  |    -> classification_source = "override"          |
  +---------------------+----------------------------+
     | no               | yes -> DONE
     v
  +--------------------------------------------------+
  | 2. MCP ANNOTATIONS                                |
  |    Did the server author declare it?              |
  |    (readOnlyHint / destructiveHint)               |
  |    -> classification_source = "annotation"        |
  +---------------------+----------------------------+
     | no               | yes -> DONE
     v
  +--------------------------------------------------+
  | 3. VERB HEURISTIC                                 |
  |    Read the first word after the service prefix.  |
  |                                                   |
  |    github_create_issue                            |
  |    ^^^^^^ strip prefix                            |
  |           ^^^^^^ verb -> "create" -> WRITE        |
  |                  ^^^^^ resource -> "issue"        |
  |    -> classification_source = "heuristic"         |
  +---------------------+----------------------------+
     | verb not known   | known -> DONE
     v
  +--------------------------------------------------+
  | 4. FAIL CLOSED -> assume WRITE                    |
  |    -> classification_source = "default"           |
  +--------------------------------------------------+
```

### Why "fail closed" matters so much

When we can't read the verb, we assume `WRITE` — **not** `READ`.
That feels backwards until you compare the two mistakes:

```
  Guess READ, but it actually writes
      -> the agent silently deletes a repository
      -> no prompt, no warning, no undo
      -> DISASTER

  Guess WRITE, but it actually reads
      -> the user sees one extra "Approve?" dialog
      -> mildly annoying
      -> RECOVERABLE
```

These two are not remotely equal in cost. So when unsure, **take the
harmless mistake.** That's what "fail closed" means.

> **Note the phrase changes meaning by context.** For classification,
> failing closed means "assume it writes". For retries (Part C), it
> means "assume you must not repeat it". The principle is the same:
> *default toward the outcome that can't hurt anyone.*

### The override table earns its keep

Failing closed creates false alarms. Four of your tools have no verb
at the front:

```
slack_auth_info            verb would be "auth"      -> unknown
slack_channel_history      verb would be "channel"   -> unknown
slack_thread_replies       verb would be "thread"    -> unknown
google_calendar_freebusy   verb would be "freebusy"  -> unknown
```

All four are harmless reads. Without an override, all four would be
marked `WRITE` and ask for approval on every call. So we correct them
by hand in `OPERATION_OVERRIDES`.

The override table also fixes **risk**, where the verb is right but
the danger is wrong:

```python
RISK_OVERRIDES = {
    "slack_send_message":               HIGH,      # cannot unsend
    "google_drive_create_permission":   HIGH,      # data exposure
    "google_drive_delete_file":         CRITICAL,  # no undo
}
```

## A4. The result on your real 61 tools

```
  OPERATIONS                    RISK LEVELS
  read     38  ############     safe      35  ##########
  write    15  #####            medium    14  ####
  delete    4  #                high       7  ##
  admin     4  #                low        3  #
                                critical   2  #

  HOW EACH WAS DECIDED
  heuristic  53   (the verb was readable)
  override    8   (a human checked it)
  default     0   <-- important: nothing fell through
```

**That `default = 0` is a test.** If you add a tool whose name the
heuristic can't read, `test_no_tool_falls_through_to_default` fails and
tells you to add an override. The test suite catches the problem before
your user does.

---

# Part B — Phase 2.3: Choosing the right tools

## B1. The pipeline

```
  "show me my githb isues"
            |
            v
  +---------------------------+
  | 1. TOKENIZE               |  break into words, drop filler
  |    [show, me, my,         |
  |     githb, isues]         |
  |    -> [show, githb, isue] |
  +-------------+-------------+
                v
  +---------------------------+
  | 2. DETECT INTENT          |  "show" -> READ
  +-------------+-------------+
                v
  +---------------------------+
  | 3. SCORE SERVICES         |  "githb" ~ "github" (0.83)
  |    github: 1.00           |  -> github wins
  +-------------+-------------+
                v
  +---------------------------+
  | 4. EXPAND TOKENS          |  fix typos ONCE, not per tool
  |    isue -> {issue: 0.80}  |
  +-------------+-------------+
                v
  +---------------------------+
  | 5. SCORE TOOLS            |  4 signals, weighted
  +-------------+-------------+
                v
  +---------------------------+
  | 6. FAIR SELECTION         |  every chosen service gets slots
  +-------------+-------------+
                v
  +---------------------------+
  | 7. CONFIDENCE / FALLBACK  |  unsure? send MORE, not fewer
  +-------------+-------------+
                v
       ~10 tools to the LLM
```

Now let's look at the interesting algorithms inside.

---

## B2. ALGORITHM 1 — Damerau-Levenshtein distance

**Job:** measure how many typing mistakes separate two words.

### The idea

Count the smallest number of single-character edits needed to turn word
A into word B. Four kinds of edit are allowed:

```
  insertion       "githb"    -> "github"     add 'u'         = 1
  deletion        "githubb"  -> "github"     remove 'b'      = 1
  substitution    "calender" -> "calendar"   'e' becomes 'a' = 1
  transposition   "githbu"   -> "github"     swap 'b' & 'u'  = 1
```

### Why the fourth one matters

Plain **Levenshtein** distance only allows the first three. It scores a
swap as **2** (one delete + one insert). But swapping two letters is
**the most common typo humans make**.

```
  "githbu" -> "github"

  Levenshtein:          distance 2   -> rejected by our threshold
  Damerau-Levenshtein:  distance 1   -> accepted
```

That one extra rule is the difference between a router that tolerates
real typing and one that doesn't.

### How it works — a grid

We fill in a table. Each cell asks *"what's the cheapest way to turn
the first `i` letters of A into the first `j` letters of B?"*

Turning **`githb`** into **`github`**:

```
            ""   g    i    t    h    u    b
       ""    0   1    2    3    4    5    6
       g     1   0    1    2    3    4    5
       i     2   1    0    1    2    3    4
       t     3   2    1    0    1    2    3
       h     4   3    2    1    0    1    2
       b     5   4    3    2    1    1    1   <-- answer: 1
```

Each cell is the **smallest** of four options:

```
  cell[i][j] = min(
      cell[i-1][j]   + 1,        delete a letter
      cell[i][j-1]   + 1,        insert a letter
      cell[i-1][j-1] + cost,     substitute (cost 0 if letters match)
      cell[i-2][j-2] + cost      transpose  (only if letters are swapped)
  )
```

The bottom-right cell is the answer.

### Two optimizations in our code

**1. Only keep 3 rows, not the whole grid.**
The transposition rule looks two rows back. Nothing looks further. So
we never store the full table — just `two_rows_back`, `previous_row`
and `current_row`. Memory goes from *rows × columns* down to
*3 × columns*.

**2. Stop early.**
If every number in the current row is already bigger than the budget we
care about, the final answer can never come back under it. So we stop
and return "further than you care about".

```python
if min(current_row) > max_distance:
    return max_distance + 1
```

This turns the worst case from *O(n×m)* into roughly
*O(n × max_distance)*.

**Code:** `damerau_levenshtein()` in `agent/text.py`

---

## B3. ALGORITHM 2 — The edit budget

**Job:** decide how many typos to forgive.

A fixed threshold is wrong in both directions:

```
  Allow 2 edits on a 3-letter word:
      "cat" matches "car", "cap", "cot", "bat"...   TOO LOOSE

  Allow only 1 edit on a 14-letter word:
      "repositoriess" rejected                       TOO STRICT
```

So the budget **scales with word length**:

```
  word length      budget      example
  -----------      ------      ------------------------
  1 - 4 chars      0 edits     (prefix rule still works)
  5 - 6 chars      1 edit      "githb"  -> "github"
  7 - 11 chars     2 edits     "calender" -> "calendar"
  12+ chars        3 edits     "repositoriess"
```

### A real bug this fixed

The first version allowed 1 edit on 4-letter words. Result:

```
  "what"  vs  "chat"     distance = 1   -> MATCHED
                                            (chat is a Slack alias)
```

**Every question starting with "what" pulled Slack into the results.**
Tightening the budget to 0 for 4-letter words killed it dead:

```python
def test_unrelated_short_words_do_not_match():
    assert fuzzy_ratio("what", "chat") == 0.0
    assert fuzzy_ratio("cat", "car") == 0.0
```

**Code:** `edit_budget()` in `agent/text.py`

---

## B4. ALGORITHM 3 — The fuzzy match ladder

**Job:** give two words a similarity score from 0.0 to 1.0.

Rules are tried in order. **First match wins.**

```
  fuzzy_ratio("githb", "github")
       |
       v
  +--------------------------------------------------+
  | Are they identical?               -> 1.00         |
  +--------------------+-----------------------------+
       | no
       v
  +--------------------------------------------------+
  | Is the short one under 3 letters?  -> 0.00        |
  | ("pr", "gh", "dm" must match exactly or not at    |
  |  all — 2-letter fuzzy hits half the dictionary)   |
  +--------------------+-----------------------------+
       | no
       v
  +--------------------------------------------------+
  | Is one a PREFIX of the other?      -> 0.94        |
  | "repo" -> "repository"  |  "doc" -> "document"    |
  +--------------------+-----------------------------+
       | no
       v
  +--------------------------------------------------+
  | Is the short one under 4 letters?  -> 0.00        |
  +--------------------+-----------------------------+
       | no
       v
  +--------------------------------------------------+
  | Is one CONTAINED in the other?     -> 0.90        |
  +--------------------+-----------------------------+
       | no
       v
  +--------------------------------------------------+
  | Within the edit budget?                           |
  |   -> 1.0 - (distance / longer_length), min 0.80   |
  |   "githb" -> 1 - 1/6 = 0.833                      |
  +--------------------+-----------------------------+
       | no
       v
                    0.00
```

### Why 0.00 and not "0.4-ish"

Giving unrelated words a small score *sounds* more sophisticated. In
practice it's just noise you have to filter out again with a threshold.

By returning a hard zero, **any non-zero score already means "this is
plausibly the same word"** — so every piece of code downstream can
trust it without re-checking.

### Real measured results

```
  githb      vs github        distance 1   -> 0.833   MATCH
  githbu     vs github        distance 1   -> 0.833   MATCH  (swap)
  calender   vs calendar      distance 1   -> 0.875   MATCH
  repo       vs repository    prefix       -> 0.940   MATCH
  what       vs chat          distance 1   -> 0.000   correctly rejected
  cat        vs car           too short    -> 0.000   correctly rejected
```

**Code:** `fuzzy_ratio()` in `agent/text.py`

---

## B5. ALGORITHM 4 — IDF (rarity weighting)

**Job:** decide how much each word in the query is worth.

### The problem

```
  "show me my github issues"
```

The word **"list"** appears in 14 of our 61 tools. It barely narrows
anything down. The word **"freebusy"** appears in exactly 1. It's
decisive.

If we treat both equally, common filler words drown out the useful
ones.

### The formula

**IDF** stands for *Inverse Document Frequency* — a standard idea from
search engines. "Document" here means "one tool".

```
                       total_tools
    idf(word) = log( 1 + ----------------- )
                        1 + tools_with_word
```

The `+1`s are "smoothing" — they stop the formula exploding when a word
appears in every tool or in none.

### Real numbers from your project (61 tools)

```
  word        appears in    idf weight    bar
  --------    ----------    ----------    ------------------
  github        18 tools       1.438      ############
  file          17 tools       1.479      ############
  get           16 tools       1.523      #############
  list          14 tools       1.623      #############
  search         5 tools       2.413      ####################
  issue          6 tools       2.274      ###################
  event          6 tools       2.274      ###################
  message        4 tools       2.580      #####################
  freebusy       1 tool        3.450      ############################
```

Read that as: **"freebusy" carries about 2.4× the weight of "github"**
when scoring. Rare words are the ones that actually identify a tool.

### How the final score uses it

A **weighted average** — not a sum:

```
              sum( idf(word) x how_well_that_word_matched )
   score  =  ----------------------------------------------
                        sum( idf(word) )
```

> **Why an average and not a sum?**
> A sum rewards long queries: a 20-word question would score higher
> than a 3-word one, so no single threshold could work for both.
> An average keeps every score on the same 0.0–1.0 scale.

**Code:** `ToolIndex.idf()` and `.lexical_score()` in `agent/index.py`

---

## B6. ALGORITHM 5 — The inverted index

**Job:** make all of this fast.

### The naive way

```
  for each of 10 query words:
      for each of 61 tools:
          for each of ~8 terms in that tool:
              fuzzy_ratio(word, term)      <-- 4,880 comparisons
```

Survivable at 61 tools. **Not** survivable at the 300+ the Agent Hub
roadmap plans for.

### The fast way — flip the question

Instead of asking *"does this tool match?"* 61 times, we ask *"which
tools contain a word like this?"* once.

We build a lookup table **at startup**, mapping every word to the tools
containing it:

```
  THE INVERTED INDEX (247 words total)

  "issue"    -> { github_list_issues, github_get_issue,
                  github_create_issue, github_update_issue,
                  github_search_issues, github_add_issue_comment }

  "file"     -> { google_drive_get_file, google_drive_read_file,
                  slack_list_files, github_get_file, ... }

  "freebusy" -> { google_calendar_freebusy }
```

### Then, per query

```
  STEP 1 — expand each word ONCE against the 247-word vocabulary

     "isue"  -> { "issue": 0.94 }
     "githb" -> { "github": 0.83 }

     ^ typos are resolved HERE, one time.
       Nothing after this point ever thinks about typos again.

  STEP 2 — look up which tools contain those words

     candidates = index["issue"] | index["github"]
                = 6 + 18 tools (with overlap)

  STEP 3 — score ONLY those candidates
     Everything else scored zero on every word.
     No reason to touch it.
```

Plus an `lru_cache` on `fuzzy_ratio`, so the same word pair is never
compared twice across the whole session.

**Measured result: 3–9 ms per query.** No network. No AI model.

**Code:** `ToolIndex` in `agent/index.py`

---

## B7. Putting it together — the scoring formula

Five signals, combined:

```
   final_score =  0.55 x lexical        do the words match?
                + 0.22 x namespace      did they name this service?
                + 0.09 x operation      read vs write vs delete?
                + 0.06 x executability  can we actually RUN it?
                + 0.08 x semantic       AI meaning match (OFF by default)
```

> **executability** is explained in [B12](#b12-executability--can-we-actually-run-it).
> Short version: a tool needing three arguments the user never gave is
> worth less than an equally relevant tool needing none.

> All four weights live at the top of `agent/router.py` as named
> constants. Weights buried inline are why routers become impossible
> to tune — you can't adjust what you can't find.

### A real worked example

Query: **`"show me my githb isues"`**

```
  github_search_issues

    lexical        0.813   "githb"~github, "isue"~issue
    namespace      1.000   "githb" identified GitHub
    operation      1.000   "show" = READ, tool is READ
    executability  0.740   needs a `query` argument

  Weights are re-normalized because semantic is off
  (0.55 + 0.22 + 0.09 + 0.06 = 0.92, so each is divided by 0.92):

    0.598 x 0.813  =  0.486
    0.239 x 1.000  =  0.239
    0.098 x 1.000  =  0.098
    0.065 x 0.740  =  0.048
                      -----
                      0.871   <-- matches the real output
```

> **Why re-normalize?** If we didn't, turning the semantic layer off
> would cap every score at 0.90 and silently change the meaning of
> every threshold in the file.

---

## B8. Multi-service — the part that needed real care

### The requirement

```
  "Find the GitHub authentication issue and check
   whether anyone discussed it in Slack."
```

One message. **Two services.** Any router that picks a single winner
cannot serve this at all.

### The trap: plain "top 10" silently fails

```
  GitHub:  18 tools, rich vocabulary  -> scores well
  Slack:   14 tools, terse docstrings -> scores lower

  Sorted purely by score, the top 10 could be:

    1. github_list_issues        0.62
    2. github_search_issues      0.61
    3. github_get_issue          0.59
    4. github_search_code        0.58
    5. github_list_repositories  0.55
    6. github_get_repository     0.54
    7. github_list_commits       0.53
    8. github_get_file           0.52
    9. github_list_branches      0.51
   10. github_get_branch         0.50
   ----------------------------------
       ZERO Slack tools
```

The agent now **physically cannot** check Slack — it was never given a
Slack tool to call. It hallucinates an answer or gives up. And it looks
like the *model* is broken when the *router* is.

### The fix: reserve slots

```
  PASS 1 — reserve 3 slots for each "primary" service

    github:  [ ][ ][ ]  <- best 3 GitHub tools
    slack:   [ ][ ][ ]  <- best 3 Slack tools

  PASS 2 — fill the remaining 4 slots by pure score

    [ ][ ][ ][ ]

  RESULT: 6 GitHub + 4 Slack   <- the agent can do the job
```

This costs a little precision. It buys **the entire multi-service
capability.**

### Which services get in?

```
  best service score = 1.00

  |<-------- 0.25 -------->|   SELECTED - each gets >= 3 reserved slots
                               beyond -> ignored

  maximum 4 services per message
```

### The tool budget grows with the services

A fixed budget is wrong in both directions:

```
  10 tools for a 1-service question   -> wasteful, hurts accuracy
  10 tools for a 4-service question   -> 2.5 tools each, unusable
```

So it scales:

```
  services      tools
  --------      -----
     1            8
     2           12
     3           16
     4           20      (hard ceiling: 24)
```

> **This is section [B13](#b13-when-the-request-touches-everything).**
> It came out of a real failure where a four-service question got
> 10 tools and one service ended up with a single unusable one.

**Code:** `_select_fairly()` in `agent/router.py`

---

## B9. The safety rule (and the trap inside it)

The router detects **intent** from the first verb in the message:

```
  "show me..."     -> READ
  "create an..."   -> WRITE
  "delete the..."  -> DELETE
  "share the..."   -> ADMIN
```

Then it scores how well each tool matches that intent:

```
  intent            tool is       score    why
  --------------    ----------    -----    -------------------------
  READ              READ           1.0     exactly what was asked
  READ              WRITE/DELETE   0.0     not what was asked
  WRITE             WRITE          1.0     exactly what was asked
  WRITE             READ           0.6     <-- READ THIS ONE
  WRITE             DELETE         0.35    mutating, but not this way
```

### The trap

> **"send a slack message to john"**

If you strip READ tools from a WRITE request — which feels tidy — the
agent breaks. Because to send a message to John, it must **first** call
`slack_list_users` to turn the name "John" into a Slack user ID.

```
  What actually has to happen:

    1. slack_list_users     (READ)   find John's ID
              |
              v
    2. slack_send_message   (WRITE)  send it
```

Remove step 1 and **every realistic multi-step task fails.** That `0.6`
is not a rounding choice — it's what makes agents work.

### The hard rule

On top of scoring, read-shaped requests **completely exclude**
`DELETE` and `ADMIN` tools:

```
  "show me my drive files"
       |
       v
  intent = READ
       |
       v
  google_drive_delete_file        REMOVED from candidates
  google_drive_create_permission  REMOVED from candidates
```

Deleting and re-sharing can't be undone. We'd rather lose a little
recall than hand the model a loaded weapon it was never asked to pick
up. `WRITE` tools stay — they're approval-gated later, and a read often
comes before one.

---

## B10. When the router is unsure — it sends MORE

This is counter-intuitive. Compare the two failure modes:

```
  TOO MANY TOOLS
     - costs extra tokens
     - model might pick a slightly worse tool
     - the agent loop gets another turn to fix it
     -> RECOVERABLE

  TOO FEW TOOLS
     - the model cannot call a tool it was never told exists
     - it hallucinates an answer, or gives up
     - nothing downstream can fix it
     -> UNRECOVERABLE
```

Those aren't equal. So **uncertainty always resolves toward breadth.**

```
  confidence = 0.4 x (best service score)
             + 0.6 x (best tool score)

  if confidence < 0.35:
        -> send ~20 tools instead of ~10
        -> if the intent was READ, only read-only tools
        -> set fallback_used = True
```

`fallback_used` is flagged, **never silent**. A rising fallback rate is
your signal that the lexicon needs new words.

---

## B11. Conversation memory

Real conversations are full of messages that name no service:

```
  User: "show me my github issues"     -> routes to GitHub
  User: "and close the first one"      -> names NOTHING
```

Routed alone, message 2 matches nothing and falls back to everything.
So we carry the previous turn's services forward at 70% strength:

```
  previous_namespaces = ("github",)
              |
              v
  "and close the first one"  -> github (0.70)
```

Two rules keep it safe:

1. **Explicit wins.** If the user names a service, the carried context
   is ignored completely.
2. **Only confident turns are remembered.** Carrying a *fallback*
   forward would pin the whole conversation to a service the router was
   never sure about.

---

## B12. Executability — can we actually RUN it?

This signal exists because of a real session. The user typed:

```
  "check github drive slack and calander connection."
```

and got this back:

```
  FAIL google_calendar_freebusy   [invalid_arguments]
       'time_min' is a required property
       'time_max' is a required property
       'calendar_ids' is a required property
```

The agent could not verify the calendar. Why? Two bugs stacked up.

### Bug 1: a command verb pretending to be a topic

Remember IDF from [B5](#b5--algorithm-4--idf-rarity-weighting): rare
words count for more. Here is what it measured:

```
  word          appears in    idf weight
  ----------    ----------    ----------
  check           1 tool         3.45     <-- treated as decisive!
  calendar       17 tools        1.52
```

The word **"check"** appears in `google_calendar_freebusy`'s docstring
and **nowhere else in 61 tools**. So IDF concluded it was a rare,
highly meaningful word — more meaningful than "calendar" itself.

But "check" is not a topic. It's a **command**. The user says "check"
the same way they'd say "show" or "get". It's already handled by intent
detection.

> **The lesson:** IDF measures rarity *in your corpus*. With only 61
> documents, an ordinary English word that happens to appear once looks
> statistically identical to a genuine technical term. Corpus size
> creates false rarity.

**The fix:** command verbs get their weight scaled to 25%.

```
  "check"  weight 3.45  ->  0.86     grammar, not topic
```

We *scale* rather than *delete* because a few intent verbs really are
topical — "search", "access" and "permission" all name real things.

### Bug 2: the router ignored whether a tool could run

Even after fixing the weight, two calendar tools were nearly tied:

```
  google_calendar_freebusy         needs time_min, time_max,
                                         calendar_ids
  google_calendar_list_calendars   needs NOTHING
```

Both are equally "about" the calendar. Only one can answer *"is my
calendar connected?"* — and the router had no way to know that, because
it never looked at the tool's arguments.

**The fix:** count required arguments the query gives no hint about.

```
                                 unfilled    score
  list_calendars                    0        1.00
  get_calendar (needs id)           1        0.74
  freebusy (needs 3)                2+       0.59   (capped)
```

An argument counts as "hinted" if its name appears in the query.
*"search files for the budget REPORT"* hints at a `query` parameter.

### Why the weight is only 0.06

This must be a **tiebreaker**, never a gate.

```
  "get the repository argus/test"

  github_get_repository needs owner + repo.
  If executability were strong, the router would prefer
  github_list_repositories - which is the wrong answer.
```

Set it too high and you always prefer lazy, argument-free tools. Set it
to zero and you get the original bug. 0.06 is the tiebreaker zone.

> **The general principle:** a tool the agent *cannot run* is worth
> less than an equally relevant tool it *can*. But relevance still
> comes first.

**Code:** `executability()` in `agent/router.py`

---

## B13. When the request touches everything

Your question: *"what if I need a 500-word prompt across all services
with 20+ tools?"*

### The old failure

```
  "check github drive slack and calander connection."

  services: github 1.00, drive 1.00, slack 1.00, calendar 0.80
  budget:   10 tools (fixed)

  RESULT:
    github    3 tools
    drive     3 tools
    slack     3 tools
    calendar  1 tool     <-- and it was the unusable one
```

Calendar scored 0.80 only because the user typed **"calander"**. That
typo cost it 0.20, which pushed it below the old "primary" cutoff of
0.10, so it got **no reserved slots at all**.

### Your idea was right

You suggested: *"minimum 3 tools of each selected service."* That is
exactly the fix, and it is now the rule:

```
  MIN_TOOLS_PER_NAMESPACE = 3      for EVERY selected service,
                                   not just the top-scoring ones
```

> If the router is confident enough to *name* a service, it is
> confident enough to give that service a **working set** of tools.

### But it needs one more piece

Your idea alone breaks against a fixed budget:

```
  4 services x 3 tools = 12 required
  but the budget was  = 10
                        ^^ two services get squeezed
```

So the budget scales too:

```
  top_k = 8 + 4 x (services - 1)      capped at 24
```

### The result on your exact query

```
  budget: 20 tools

    github     3 tools   get_authenticated_user, list_repositories, ...
    drive      6 tools   search_files, get_file, list_folder, ...
    slack      8 tools   auth_info, list_channels, list_users, ...
    calendar   3 tools   freebusy, list_calendars, get_calendar
                                    ^^^^^^^^^^^^^^ the one that works
```

Every service is now usable. And because services are processed
**best-first**, if the budget ever runs short it is the *weakest*
service that gets squeezed — never the strongest.

### For a 500-word prompt

Long prompts are handled by the same machinery, because routing scores
on **which words matched**, not on how many words there are — that is
why [B5](#b5--algorithm-4--idf-rarity-weighting) uses a weighted
*average* rather than a sum. A 500-word prompt naming all four services
gets the full 20-tool budget; the extra 480 words of context simply do
not distort the scores.

**Code:** `BASE_TOP_K`, `MIN_TOOLS_PER_NAMESPACE` in `agent/router.py`

---

## B14. Second-chance retrieval

Your other idea: *"retry, but filter out all the tools already
chosen."* This is now built in, and it is a genuinely good instinct.

The router picks tools **before** the model has tried anything.
Sometimes that guess is only revealed as wrong by running it.

```
  ROUND 1
    model calls google_calendar_freebusy
      -> FAIL invalid_arguments
    model calls google_calendar_freebusy again
      -> FAIL invalid_arguments
                |
                v
    every tool call this round failed
                |
                v
  ESCALATE: re-route the SAME question,
            excluding the tools already offered
                |
                v
  ROUND 2
    model now also has google_calendar_list_calendars
      -> OK
```

Note what is being retried: **the retrieval, not the call.** Phase 2.7
retries the same call when the failure was transient. This retries the
*choice of tools* when the failure was a bad pick.

Two limits keep it honest:

```
  MAX_ESCALATIONS = 2     a third pass is reaching into tools the
                          router scored badly for good reason

  triggers only when      a round with SOME successes is making
  a round has ZERO        progress; don't widen for that
  successes
```

`AgentTurn.escalations` records how often it fired. **A non-zero value
is a tuning signal** — it means the router's first pick was wrong, so
check `agent/lexicon.py`.

**Code:** `run_agent()` in `agent/loop.py`, `exclude=` in `route()`

---

# Part C — Phase 2.5–2.8: Running tools safely

## C1. The 8-step pipeline

```
  LLM says: call github_create_issue(owner="a", repo="b")
                          |
                          v
  +-----------------------------------------------------+
  | 1. Does the tool exist?                              |
  |    no -> TOOL_NOT_FOUND                              |
  +-----------------------------------------------------+
  | 2. Was it offered this turn?                         |
  |    no -> TOOL_NOT_AVAILABLE                          |
  +-----------------------------------------------------+
  | 3. Are the arguments valid? (JSON Schema)            |
  |    no -> INVALID_ARGUMENTS                           |
  +-----------------------------------------------------+
  | 4. Is this agent permitted?                          |
  |    no -> PERMISSION_DENIED                           |
  +-----------------------------------------------------+
  | 5. Does a human need to approve?                     |
  |    declined -> APPROVAL_DENIED                       |
  +=====================================================+
  | 6. RUN IT (with a 30s timeout)   <- first network I/O|
  +-----------------------------------------------------+
  | 7. What kind of failure was it?                      |
  +-----------------------------------------------------+
  | 8. Retry — only if provably safe                     |
  +-----------------------------------------------------+
                          |
                          v
                   ExecutionRecord
```

### The order is the design

Steps 1–5 are **free**. No network. No credentials. No waiting.

```
  A misspelled tool name:
      caught at step 1  ->  microseconds
      caught at step 6  ->  a 30-second timeout

  A forbidden action:
      caught at step 4  ->  never leaves the process
      caught at step 6  ->  already happened
```

> **Rule of thumb:** reject as early and as cheaply as you can.

---

## C2. Errors are values, not exceptions

Your instinct will be `raise ToolExecutionError(...)`. **Don't.**

An exception means *"abandon this work and unwind the stack."* But a
failed tool call is not abandoned work — **the failure is the answer**,
and it must go back to the AI so it can fix itself:

```
  Agent: github_get_repository(owner="argus", repo="tset")
                                                    ^^^^ typo
              |
              v
  Tool:  { "success": false,
           "error": { "type": "not_found",
                      "hint": "Check identifiers for typos" } }
              |
              v
  Agent: github_get_repository(owner="argus", repo="test")
                                                    ^^^^ fixed!
```

If we raised an exception, **that recovery loop could not exist.**

> **Rule of thumb:**
> Exceptions are for *"the program is broken."*
> Values are for *"the world said no."*

The MCP specification agrees — it says tool errors *SHOULD* be reported
inside the result, not as protocol errors.

---

## C3. Failure arrives in three disguises

A correct executor checks **all three**. Miss one and you silently
record failures as successes.

```
  DISGUISE 1 — an exception was raised
      the socket died, the process vanished
      -> caught by try/except

  DISGUISE 2 — isError is set on the result
      MCP's own error channel
      -> caught by checking raw.is_error

  DISGUISE 3 — the result looks perfect, but the payload says
               {"success": false, ...}
      -> caught by reading INSIDE the payload
```

### Disguise 3 is your normal case

Your `@github_tool` and `@slack_tool` wrappers **catch exceptions and
return a dictionary**:

```python
except GitHubError as exc:
    return {"success": False, "error": {...}}    # <-- a normal return!
```

As far as MCP is concerned, that call **succeeded perfectly**. No
error flag. The failure is hiding inside the data.

An executor that only checked exceptions would report a **completely
broken GitHub integration as 100% healthy.**

### And your four services disagree on the shape

```
  github     error = {"type", "message", "status_code", "details"}
  calendar   error = {"type", "message"}
  slack      error = "a plain string"
  drive      error = "a plain string"  +  a separate "code" field
```

`classify_payload()` reads all four and produces **one** `ToolError`.
This is what "normalize at the boundary" means in practice.

---

## C4. The 16 error codes

```
  OUR SIDE                    caught before any network call
  ------------------------    -------------------------------
  TOOL_NOT_FOUND              no such tool
  TOOL_NOT_AVAILABLE          not routed this turn
  INVALID_ARGUMENTS           failed schema check
  PERMISSION_DENIED           policy said no
  APPROVAL_DENIED             human said no

  TRANSPORT                   never completed a round trip
  ------------------------    -------------------------------
  CONNECTION_ERROR            couldn't reach the server
  TIMEOUT                     no reply in time
  SERVER_UNAVAILABLE          server closed the pipe
  MALFORMED_RESPONSE          couldn't parse the reply
  CANCELLED                   we were shut down

  REMOTE SERVICE              we reached it and it said no
  ------------------------    -------------------------------
  AUTHENTICATION_FAILED       401 — bad or expired token
  AUTHORIZATION_FAILED        403 — no access to this resource
  NOT_FOUND                   404
  VALIDATION_ERROR            400 / 422
  RATE_LIMITED                429
  SERVICE_ERROR               500

  UNKNOWN                     failed, but we can't tell why
```

### Every code carries a hint for the model

```python
AUTHENTICATION_FAILED -> "Do not retry. Tell the user they need
                          to reconnect the service."
NOT_FOUND             -> "Check identifiers for typos, or search
                          for the correct one first."
```

This is the difference between an agent that retries the same broken
call five times and one that fixes it. A test asserts **every** code
has a hint, so a new code can't ship without one.

---

## C5. ALGORITHM 6 — The two-tier retry rule

This is the most important safety logic in Phase 2.

### The naive rule (from Phase2.md)

```
  timeout          -> retry
  connection error -> retry
  429 rate limit   -> retry with backoff
  401 unauthorized -> don't
  403 forbidden    -> don't
  invalid args     -> don't
```

Correct, but **incomplete** — and the missing piece causes real damage.

### The question you must actually ask

> ### Do we **KNOW** the request never took effect?

Picture `github_create_issue` timing out after 30 seconds:

```
   our agent  ----- create issue ---->  GitHub
                                          |
                                     issue CREATED
                                          |
   our agent  <-- response lost --------  X   (network hiccup)
        |
        v
   we see: TIMEOUT
```

The timeout tells us the **response** never arrived. It tells us
**nothing** about whether the **request** was processed.

Retry, and your client gets **two identical GitHub issues** — and it
looks like your agent is malfunctioning.

### So retryability has two tiers

```
  +---------------------------------------------------------+
  |  TIER 1 — ALWAYS RETRYABLE                               |
  |  These PROVE the request never ran.                      |
  |                                                          |
  |    CONNECTION_ERROR   never reached the service          |
  |    SERVER_UNAVAILABLE rejected at the door               |
  |    RATE_LIMITED       429 = "I rejected this"            |
  |                                                          |
  |  Safe for READ, WRITE, DELETE — anything.                |
  +---------------------------------------------------------+

  +---------------------------------------------------------+
  |  TIER 2 — AMBIGUOUS                                      |
  |  Might have taken effect. We cannot tell.                |
  |                                                          |
  |    TIMEOUT                                               |
  |    SERVICE_ERROR                                         |
  |    MALFORMED_RESPONSE                                    |
  |                                                          |
  |  Safe ONLY for READ. Repeating a read harms nothing.     |
  +---------------------------------------------------------+

  +---------------------------------------------------------+
  |  EVERYTHING ELSE — NEVER retried                         |
  |  Including UNKNOWN. Retrying something you don't         |
  |  understand is how a Slack message gets sent 3 times.    |
  +---------------------------------------------------------+
```

### The decision tree

```
                    a call failed
                         |
                         v
              is the code in TIER 1?
                    /         \
                 yes           no
                  |             |
                  v             v
               RETRY    is the code in TIER 2?
                              /         \
                           yes           no
                            |             |
                            v             v
                  is the tool a READ?   DON'T RETRY
                      /         \
                   yes           no
                    |             |
                    v             v
                 RETRY      DON'T RETRY
```

### Seen side by side

```
  github_list_issues   + TIMEOUT  ->  RETRY      (a read; harmless)
  github_create_issue  + TIMEOUT  ->  NO RETRY   (might duplicate!)
  github_create_issue  + 429      ->  RETRY      (provably rejected)
  github_list_issues   + 401      ->  NO RETRY   (won't fix itself)
```

### One more safety detail

```python
def is_retryable(code, operation = Operation.WRITE):   # <-- default
```

The default is `WRITE`, the **dangerous** one. A caller who forgets to
pass the operation gets the conservative answer, not the permissive
one. Defaults should fail toward the harmless outcome.

**Code:** `is_retryable()` in `agent/errors.py`

---

## C6. ALGORITHM 7 — Exponential backoff with jitter

**Job:** decide how long to wait before trying again.

```
  attempt 1 fails -> wait ~0.5s
  attempt 2 fails -> wait ~1.0s
  attempt 3 fails -> wait ~2.0s     (capped at 8s)
```

### Why exponential?

If a service is struggling, hammering it at a fixed interval makes
things **worse**. Backing off gives it room to recover.

### Why capped?

Without a cap, `2^10` seconds is 17 minutes. No user waits that long.

### Why jitter (a little randomness)?

This one is easy to miss and matters at scale.

```
  WITHOUT JITTER — everyone retries at the same instant

    service goes down
         |
    t=0.5s   |||||||||||||||||||   200 agents retry AT ONCE
         |                          -> service falls over again
    t=1.0s   |||||||||||||||||||   200 agents retry AT ONCE
         |                          -> service falls over again


  WITH JITTER — retries are spread out

    t=0.5s   | |  |   ||  |  |     spread across 0.5-0.625s
         |                          -> service recovers
```

That pile-up is called a **thundering herd**. A little randomness is
the entire fix:

```python
return delay + random.uniform(0.0, delay * 0.25)
```

And if the service **told us** how long to wait (`retry_after`), that
always wins — an explicit instruction beats our guess.

**Code:** `_backoff_delay()` in `agent/executor.py`

---

## C7. Permissions and approval — two different questions

These are deliberately kept apart:

```
  PermissionPolicy    "MAY this agent ever use this tool?"
                      -> configuration, decided once per agent

  ApprovalHandler     "should we do THIS PARTICULAR call right now?"
                      -> a human judgement, made in the moment
```

> Permission is about **capability**. Approval is about **consent**.
> An agent can hold permission to send Slack messages and *still* need
> a human to okay this specific message to this specific customer.

### Policies you can use today

```
  AllowAllPolicy      no restrictions (the default)

  ReadOnlyPolicy      blocks everything that changes state.
                      Demo an agent on a client's real account with a
                      hard guarantee it cannot modify anything — a
                      guarantee that lives BELOW the AI, so no prompt
                      injection can talk its way past it.

  MaxRiskPolicy       "may write, but nothing CRITICAL"

  ScopePolicy         grant-based, the Phase 5 shape:
                        {"github:*:read"}       all GitHub reads
                        {"github:issue:write"}  only issues
```

### One async gotcha worth learning

`ConsoleApproval` wraps `input()` in `asyncio.to_thread`. That is not
decoration:

```
  WITHOUT to_thread:

     input() blocks the thread it runs on.
     In async code, that thread IS the event loop.
     So EVERYTHING freezes — timers, the MCP connection's background
     reads, every other user's request.
```

> **Rule of thumb:** never call a blocking function directly inside
> async code. Wrap it in `asyncio.to_thread`.

---

## C8. Execution records (Phase 2.8)

Every call produces one record, whether it succeeded, failed, was
denied, or was never attempted:

```json
{
  "execution_id": "exec_7417a3c40fa2",
  "tool": "github_list_issues",
  "namespace": "github",
  "operation": "read",
  "risk_level": "safe",
  "status": "success",
  "started_at": "2026-08-18T09:52:55.176190+00:00",
  "duration_ms": 0.16,
  "attempts": 1,
  "approved_by_user": null,
  "error": null
}
```

Three details worth copying into your own projects:

**1. Timestamps are always UTC.**
These records outlive the machine that made them. A timestamp without a
timezone is a bug waiting for your first deployment in another region.

**2. `DENIED` is a separate status from `FAILED`.**
"The user said no" and "GitHub returned a 500" are both non-successes,
but one is the system working correctly and the other is an incident.
Merging them would make your future error dashboard lie to you.

**3. Redaction happens where the record is BUILT.**

```
  api_key = "sk-live-123456"    ->  "***redacted***"
  text    = 5000 characters     ->  first 120 + "... (5000 chars)"
```

Two problems solved: **secrets** (log a token once and it's in your log
system forever, usually somewhere you can't delete from) and **volume**
(`slack_send_message` carries the whole message body — storing that
turns a telemetry table into a copy of your user's content).

Redacting at the *print* site relies on every future caller
remembering. One of them won't.

This is the data behind the timeline panel in `AI_Agent_Hub.md`
section 13:

```
Tool executions (3 agent rounds)
  OK   github_list_issues                 421ms
  FAIL google_drive_get_file               88ms  [not_found]

  2 calls, 1 failed, 509ms in tools
```

---

# 7. Following one message end to end

Let's trace **`"show me my githb isues"`** all the way through.

```
STEP 1  TOKENIZE                                    agent/text.py
        "show me my githb isues"
          -> [show, me, my, githb, isues]
          -> drop stopwords (me, my)
          -> singularize: isues -> isue
          = [show, githb, isue]

STEP 2  DETECT INTENT                             agent/router.py
        "show" is in the READ verb list
          -> intent = READ

STEP 3  SCORE SERVICES                           agent/lexicon.py
        "githb" vs alias "git"     -> 0.940  MATCH (prefix)
        "githb" vs alias "github"  -> 0.833  MATCH (1 typo)
        "isue"  vs alias "issue"   -> 0.800  MATCH (1 typo)

        best hit = 0.940
        3 independent aliases agreed -> +0.10 bonus
          -> github: 1.00   (capped; only service above threshold)

STEP 4  EXPAND TOKENS                              agent/index.py
        "githb" -> { github: 0.833, git: 0.940 }
        "isue"  -> { issue:  0.800 }
        "show"  -> {} (out of vocabulary — an intent verb,
                       so NOT reported as a missing alias)

STEP 5  BUILD CANDIDATE POOL                       agent/index.py
        all 18 GitHub tools
        + any tool containing "github" or "issue"

STEP 6  SAFETY FILTER                             agent/router.py
        intent is READ
          -> remove all DELETE and ADMIN tools

STEP 7  SCORE EACH TOOL                           agent/router.py
        github_list_issues:
            lexical   0.813  x 0.611  =  0.497
            namespace 1.000  x 0.278  =  0.278
            operation 1.000  x 0.111  =  0.111
                                        -------
                                         0.886

STEP 8  RANK + FLOOR + FAIR SELECTION             agent/router.py
        sort by score
        drop anything below 45% of the best
        reserve slots per primary service
          -> 10 tools selected

STEP 9  CONFIDENCE                                agent/router.py
        0.4 x 1.000 (best service)
      + 0.6 x 0.886 (best tool)     = 0.931
        well above 0.35, so no fallback

STEP 10 HAND TO THE LLM                             agent/loop.py
        convert 10 MCP tools to Ollama schema
        allowed_tools = those 10 names

STEP 11 THE MODEL CHOOSES                              (Ollama)
        "call github_list_issues(owner=..., repo=...)"

STEP 12 EXECUTE                                 agent/executor.py
        1. exists?           yes
        2. was it offered?   yes
        3. arguments valid?  yes (JSON Schema)
        4. permitted?        yes
        5. needs approval?   no (it's a READ)
        6. run with timeout  ok, 421ms
        7. check payload     no "success": false
        8. no retry needed
          -> ExecutionRecord(status=SUCCESS, 421ms)

STEP 13 BACK TO THE MODEL                           agent/loop.py
        append the result as a "tool" message
        loop again -> the model now writes the final answer
```

---

# 8. Troubleshooting guide

This is the section to come back to when something breaks.

## 8.1 Routing problems

### Symptom: "The router picks the wrong service"

```
  1. LOOK AT THE CONSOLE OUTPUT

     [Router] Services    : slack (1.00), github (0.82)
                            ^^^^^ wrong one won

  2. FIND OUT WHICH WORD DID IT

     python -c "
     from tests.tool_fixtures import build_router
     r = build_router()
     d = r.route('YOUR QUERY HERE')
     for n in d.namespace_scores:
         print(n.namespace, n.score, n.matched_aliases)
     "

     matched_aliases tells you EXACTLY which word matched.

  3. FIX

     Wrong word is in the alias list  -> remove it from
                                         NAMESPACE_ALIASES

     Right word is missing            -> add it to the correct
                                         service's alias list
```

**File to edit:** `agent/lexicon.py`

---

### Symptom: "A typo isn't being recognized"

```
  1. TEST THE WORD PAIR DIRECTLY

     python -c "
     from agent.text import fuzzy_ratio, damerau_levenshtein, edit_budget
     a, b = 'yourtypo', 'correctword'
     print('distance:', damerau_levenshtein(a, b))
     print('budget  :', edit_budget(max(len(a), len(b))))
     print('score   :', fuzzy_ratio(a, b))
     "

  2. READ THE RESULT

     distance > budget    -> too many typos for that word length.
                             This is usually CORRECT behaviour.
                             Loosening it causes false matches.

     score == 0.0 and
     both words < 5 chars -> short words need an exact match
                             by design. Add the misspelling as
                             its own alias instead.

  3. THE SAFE FIX

     Do NOT loosen edit_budget — that is what caused the
     "what" / "chat" bug. Instead add the common misspelling
     directly to NAMESPACE_ALIASES.
```

---

### Symptom: "It sends all 20 tools / fallback keeps triggering"

```
  [Router] LOW CONFIDENCE - widened the tool set.

  This means confidence < 0.35. It is the router telling you
  honestly that it does not know.

  1. CHECK THE UNMATCHED LINE

     [Router] Unmatched   : standup, retro

     Those are words no tool knows. THAT is your fix list.

  2. ADD THEM

     "standup"  -> NAMESPACE_ALIASES["slack"]
     "retro"    -> NAMESPACE_ALIASES["google_calendar"]

  3. RE-RUN

     python tests/run_tests.py router
```

> A rising fallback rate is a **feature**, not a bug. It is your
> lexicon telling you what real users actually say.

---

### Symptom: "The right tool exists but never gets selected"

```
  1. IS IT IN THE POOL AT ALL?

     python -c "
     from tests.tool_fixtures import build_router
     r = build_router()
     d = r.route('YOUR QUERY')
     print([c.tool_name for c in d.candidates])
     "

  2. CHECK ITS SCORE BREAKDOWN

     for c in d.candidates:
         print(c.tool_name, c.breakdown.to_dict())

     lexical near 0    -> the tool's name/description share no
                          words with the query.
                          FIX: add words to TOOL_KEYWORDS
                               in agent/lexicon.py

     namespace is 0    -> the service wasn't detected.
                          FIX: see the first symptom above

     operation is 0    -> intent mismatch. A READ query will
                          never surface DELETE/ADMIN tools.
                          This is intentional.
```

---

### Symptom: "A service got only one tool / a multi-service request failed"

```
  1. CHECK THE SERVICE SCORES

     [Router] Services : github (1.00), ..., google_calendar (0.80)
                                                              ^^^^
     A typo costs score. "calander" scores 0.80 instead of 1.00.

  2. CHECK THE PER-SERVICE COUNT

     python -c "
     from tests.tool_fixtures import build_router
     d = build_router().route('YOUR QUERY')
     print('budget:', len(d.candidates))
     for ns, tools in d.by_namespace().items():
         print(f'  {ns}: {len(tools)}')
     "

  3. EXPECTED

     Every selected service should have >= MIN_TOOLS_PER_NAMESPACE.
     Budget should be 8 + 4 x (services - 1), capped at 24.

  4. IF A SERVICE IS SHORT

     It was squeezed by the budget. Either raise
     TOP_K_PER_EXTRA_SERVICE, or add the misspelling to
     NAMESPACE_ALIASES so the service scores full marks.
```

---

### Symptom: "The agent picked a tool it could not supply arguments for"

```
  1. THIS IS WHAT THE executability SIGNAL IS FOR

     python -c "
     from tests.tool_fixtures import build_router
     d = build_router().route('YOUR QUERY')
     for c in d.candidates[:5]:
         print(c.tool_name, c.breakdown.to_dict())
     "

     executability 1.00 -> needs nothing, or the query hinted at it
     executability 0.74 -> one unsupplied required argument
     executability 0.59 -> two or more

  2. IF A NEEDY TOOL STILL WINS

     Its lexical score is genuinely higher. Check whether a generic
     command word is inflating it - that was the "check" bug.
     Look at INTENT_VERB_WEIGHT_SCALE.

  3. IF A NEEDY TOOL IS WRONGLY LOSING

     UNFILLED_ARGUMENT_PENALTY is too high. Lower it.
     It must break ties, never decide them.
```

---

## 8.2 Classification problems

### Symptom: "A tool asks for approval when it shouldn't"

```
  1. CHECK HOW IT WAS CLASSIFIED

     python -c "
     from tests.tool_fixtures import build_tool_definitions
     t = {x.name: x for x in build_tool_definitions()}['TOOL_NAME']
     print('operation:', t.operation)
     print('risk     :', t.risk_level)
     print('source   :', t.classification_source)
     print('approval :', t.requires_approval)
     "

  2. READ THE 'source' FIELD — it tells you which rule fired

     "default"    -> the verb was unreadable, so it FAILED CLOSED
                     to WRITE. This is the fail-safe working.
                     FIX: add it to OPERATION_OVERRIDES

     "heuristic"  -> the verb was read but is in the wrong list.
                     FIX: check READ_VERBS / WRITE_VERBS /
                          DELETE_VERBS in agent/classification.py

     "override"   -> a human set this. Edit OPERATION_OVERRIDES.

     "annotation" -> the MCP server declared it. Fix it at the
                     server, in the @mcp.tool() decorator.
```

**File to edit:** `agent/classification.py`

---

### Symptom: "A dangerous tool has a low risk level"

```
  Risk defaults come from the operation:

     READ   -> SAFE      WRITE  -> MEDIUM
     DELETE -> HIGH      ADMIN  -> HIGH

  If that default is wrong for a specific tool, add it to
  RISK_OVERRIDES. Ask yourself two questions:

     1. Can it be undone?         no  -> raise the risk
     2. Do other people see it?   yes -> raise the risk

  Example already in the table:

     slack_send_message  is a WRITE (default MEDIUM)
                         but you cannot unsend it
                         and a customer sees it
                         -> overridden to HIGH
```

---

## 8.3 Execution problems

### Symptom: "The agent says a tool failed, but it looks fine"

```
  1. CHECK THE ERROR CODE IN THE TIMELINE

     FAIL github_get_repository    88ms  [not_found]
                                          ^^^^^^^^^

  2. LOOK UP WHAT THAT MEANS

     tool_not_found       -> the tool name doesn't exist.
                             Typo, or discovery didn't run.

     tool_not_available   -> the router didn't select it this
                             turn. The model guessed a name.
                             Usually harmless; the model retries.

     invalid_arguments    -> our JSON Schema check rejected it
                             BEFORE calling. The message names
                             the exact bad field.

     unknown              -> we could tell it failed but not
                             why. Usually means the service
                             returned an unrecognizable error.
                             See "Known gaps" for the Drive bug.
```

---

### Symptom: "A write happened twice"

```
  THIS SHOULD BE IMPOSSIBLE. If you see it, check:

  1. Was the operation classified as READ by mistake?

     python -c "
     from tests.tool_fixtures import build_tool_definitions
     t = {x.name: x for x in build_tool_definitions()}['TOOL_NAME']
     print(t.operation)     # must NOT be 'read'
     "

     A write tool misclassified as READ becomes eligible for
     timeout retries. THAT is the bug — fix the classification,
     not the retry logic.

  2. Did someone add a code to ALWAYS_RETRYABLE?

     Only add a code there if it PROVES the request never ran.
     When in doubt, put it in AMBIGUOUS_RETRYABLE instead.
```

---

### Symptom: "Calls are slow / hang"

```
  1. CHECK THE ATTEMPT COUNT IN THE TIMELINE

     FAIL github_list_issues   90200ms  (3 attempts)
                                         ^^^^^^^^^^

     3 attempts x 30s timeout + backoff = ~90 seconds.

  2. TUNE IT for interactive use — in agent/executor.py or
     when constructing AgentEngine:

     AgentEngine(timeout_seconds=10.0, max_attempts=2)

  3. IS ROUTING THE SLOW PART? (it shouldn't be)

     [Router] Confidence : 0.87  (4.3 ms)
                                  ^^^^^^ should be under 15ms

     If it's much higher, the index is being rebuilt every
     query. Check that registry.version isn't changing.
```

---

### Symptom: "Nothing gets approved / everything gets approved"

```
  requires_approval is TRUE when:

      the tool mutates state        (WRITE / DELETE / ADMIN)
   OR the risk is HIGH or CRITICAL  (even for a READ)

  Everything asks    -> your approval handler is ConsoleApproval
                        and most tools are mutating. Expected.
                        Use AutoApprove for unattended runs.

  Nothing asks       -> you are using AutoApprove (the default).
                        Pass approval=ConsoleApproval() to
                        AgentEngine.
```

---

## 8.4 The universal debugging recipe

When you don't know where the problem is, print the whole decision:

```python
python -c "
import json
from tests.tool_fixtures import build_router
r = build_router()
d = r.route('YOUR QUERY HERE')
print(json.dumps(d.to_dict(), indent=2))
"
```

That single command shows you: intent, every service score, every
matched alias, every candidate tool with its full score breakdown,
unmatched words, confidence, and timing.

**Everything in the router is explainable on purpose.** You should
never have to guess.

---

# 9. How to test and tune

## 9.1 Running the tests

```bash
python tests/run_tests.py              # all 120
python tests/run_tests.py router       # text, classification, routing
python tests/run_tests.py executor     # errors, retries, pipeline
```

> The tests are written as plain functions with `assert`, so they work
> with **both** our zero-dependency runner **and** `pytest` once you
> install it. Nothing needs to change.

## 9.2 The tests are built from your real code

`tests/tool_fixtures.py` reads `services/*/tools.py` with Python's
`ast` module and extracts every tool's real name, real docstring, and
real parameter list.

```
  WHY NOT just import the modules?
     Importing services.github.tools constructs GitHubService(),
     which reads credentials and opens network clients. Tests must
     not need a GitHub token to check that a router ranks correctly.

  WHY NOT a hardcoded list of 61 tools?
     It would drift. Add github_delete_repository tomorrow and the
     tests would keep passing against a tool set that no longer
     exists. Parsing means the fixture is ALWAYS in sync.
```

## 9.3 Where tuning actually happens

> ### Almost all routing quality lives in `agent/lexicon.py`.

An extra alias beats any amount of algorithmic cleverness. No algorithm
can guess that your client says **"standup"** when they mean a calendar
event.

Your loop:

```
  1. Use the agent normally
  2. Watch the [Router] Unmatched line
  3. Add those words to NAMESPACE_ALIASES or TOOL_KEYWORDS
  4. python tests/run_tests.py router
  5. Repeat
```

## 9.4 The knobs, and when to turn them

| Constant | File | Default | Turn it when |
|---|---|---|---|
| `BASE_TOP_K` | `router.py` | 8 | Single-service answers miss tools (raise) |
| `TOP_K_PER_EXTRA_SERVICE` | `router.py` | 4 | Multi-service answers miss tools (raise) |
| `MAX_TOP_K` | `router.py` | 24 | Hard ceiling on tools per message |
| `MIN_TOOLS_PER_NAMESPACE` | `router.py` | 3 | A service keeps getting too few (raise) |
| `RELATIVE_SCORE_FLOOR` | `router.py` | 0.45 | Too many weak tools (raise) |
| `FALLBACK_CONFIDENCE` | `router.py` | 0.35 | Fallback fires too often (lower) |
| `INTENT_VERB_WEIGHT_SCALE` | `router.py` | 0.25 | A command word is skewing results (lower) |
| `UNFILLED_ARGUMENT_PENALTY` | `router.py` | 0.35 | Argument-heavy tools rank too high (raise) |
| `WEIGHT_*` | `router.py` | .55/.22/.09/.06/.08 | Only after checking the breakdown |
| `MAX_ESCALATIONS` | `loop.py` | 2 | Turns take too long (lower) |
| `DEFAULT_TIMEOUT_SECONDS` | `executor.py` | 30.0 | Interactive use feels slow (lower) |
| `DEFAULT_MAX_ATTEMPTS` | `executor.py` | 3 | Retries take too long (lower) |
| `coerce_types` | `executor.py` | True | You want strict type rejection (False) |

> **Change one knob at a time and re-run the tests.** If a test fails,
> that test is telling you the change broke a real guarantee.

---

# 10. Glossary

| Term | Plain meaning |
|---|---|
| **MCP** | Model Context Protocol — a standard way for AI clients to call tools |
| **Tool** | One callable function, e.g. `github_list_issues` |
| **Namespace** | Which service a tool belongs to: `github`, `slack`, ... |
| **Registry** | The one place all tools are stored |
| **Routing** | Choosing which tools to show the AI for this message |
| **Token** | One word-ish chunk of text |
| **Stopword** | A filler word with no meaning for us (`the`, `my`, `is`) |
| **Stemming / singularize** | Reducing a word to its base form (`issues` → `issue`) |
| **Edit distance** | How many typing mistakes separate two words |
| **Transposition** | Two adjacent letters swapped (`githbu`) |
| **Fuzzy match** | Matching words that are close but not identical |
| **IDF** | Inverse Document Frequency — rare words count for more |
| **Inverted index** | A word → tools lookup table, built once for speed |
| **Embedding** | A list of numbers representing a text's meaning |
| **Cosine similarity** | How similar two embeddings are (0.0 to 1.0) |
| **Idempotent** | Safe to repeat — doing it twice equals doing it once |
| **Backoff** | Waiting longer between each retry |
| **Jitter** | Small randomness added to a delay |
| **Thundering herd** | Many clients retrying at the same instant |
| **Fail closed** | When unsure, choose the option that can't cause harm |
| **Seam** | A small interface you can swap implementations behind |
| **Null object** | A do-nothing implementation, so callers skip `if x is None` |
| **Blast radius** | How far the damage spreads if something goes wrong |

---

# 11. Known gaps

Honest list of what is **not** finished. None of these block Phase 3.

### 11.1 Drive loses all error detail — real bug

`services/google_drive/tools.py` routes errors through
`handle_tool_error()`, but that function only recognizes
`MCPApplicationError` — and `GoogleDriveError` does not inherit from
it. So **every** Drive failure collapses to:

```json
{"success": false, "error": "Internal tool error", "code": "INTERNAL_ERROR"}
```

A 404 and an expired token look identical. The executor can only
classify them as `UNKNOWN`, and therefore never retries them.

**The fix is one line** in `services/google_drive/errors.py`:

```python
from core.errors import MCPApplicationError

class GoogleDriveError(MCPApplicationError):
    ...
```

### 11.2 Slack returns errors as flat strings

```python
return {"success": False, "error": str(exc)}
```

Status codes are lost. The executor recovers intent from keywords, but
adopting GitHub's `{"type", "message", "status_code"}` shape across all
four services would make classification **exact** instead of inferred.

### 11.3 The MCP server sends no annotations

Adding hints to the ~23 mutating tools would promote classification
from *guessing* to *authoritative*:

```python
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def google_drive_delete_file(...): ...
```

The client code already handles this. **Nothing needs to change on our
side** the day you add them — that is why the annotation branch exists
even though it never fires today.

### 11.4 Slack has no message-search tool

The Phase 2 milestone in `Phase2.md` assumes `slack_search_messages`,
which does not exist. The router correctly falls back to
`slack_channel_history`, but that needs a `channel_id`, so the agent
must call `slack_list_channels` first.

### 11.5 Drive can grant access but not revoke it

You have `create_permission` and `list_permissions`, but no delete.
*"Remove Bob's access"* routes sensibly but cannot be fulfilled.

### 11.6 Descriptions are one line each

This caps how well a word-matching router can work. Rather than
rewriting 61 docstrings — they are API documentation for the AI, and
stuffing synonyms in would degrade what they are *for* — routing
vocabulary lives separately in `TOOL_KEYWORDS`.

---

## Phase 2 status

| Sub-phase | What it is | Status |
|---|---|---|
| 2.1 | Tool discovery + registry | done |
| 2.2 | Normalization + classification | done |
| 2.3 | Hybrid typo-tolerant routing | done |
| 2.4 | Tool ranking | done |
| 2.5 | Tool executor | done |
| 2.6 | Error handling | done |
| 2.7 | Retry with backoff | done |
| 2.8 | Execution metadata | done |
| 2.9 | Agent loop | done |

**120 tests passing. No new dependencies added.**

Next up is **Phase 3 — the backend**: FastAPI, PostgreSQL, auth,
agents, conversations. The engine is already shaped for it. The router
and executor hold no per-conversation state and take everything as
arguments, so one instance can safely serve many users at once.
`ExecutionRecord.to_dict()` is your `tool_executions` table, and
`ScopePolicy` is what your `permissions` table will feed.
