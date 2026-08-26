from __future__ import annotations

from .schemas import Operation

"""
Domain vocabulary — the bridge between how humans talk and how tools
are named.

This file is **data, not logic**. That separation is intentional:

- Adding a new service should mean adding a dict entry here, not
  editing the router.
- A non-programmer (or a future admin UI) can extend the vocabulary
  without touching scoring code.
- The router stays small enough to hold in your head.

The single biggest quality lever in the whole routing system lives in
this file. An extra alias here is worth more than any amount of
scoring cleverness, because no algorithm can guess that your users say
"standup" when they mean "calendar event".
"""


# ---------------------------------------------------------------------
# Namespace aliases
# ---------------------------------------------------------------------
#
# Maps a service to every word a user might plausibly use for it.
#
# These do NOT have to be exclusive. "file" points at Drive, but Slack
# and GitHub have file tools too. That is fine and is worth
# understanding clearly:
#
#   A namespace alias is a PRIOR, not a filter.
#
# Matching "file" boosts Drive's tools; it never removes Slack's. The
# tool-level scoring stage still gets to promote `slack_list_files` if
# the query also said "slack". Treating these as hard filters is the
# classic mistake that makes routers brittle.

NAMESPACE_ALIASES: dict[str, tuple[str, ...]] = {
    "github": (
        "github",
        "git hub",
        "gh",
        "git",
        "repo",
        "repository",
        "codebase",
        "commit",
        "branch",
        "issue",
        "ticket",
        "bug",
        "pull request",
        "pullrequest",
        "pr",
        "merge",
        "code",
        "source code",
        # Words that name a GitHub capability without naming GitHub.
        # Without these, "is CI passing on main?" detects NO service,
        # and a query with no service scores zero confidence - so the
        # router discards its ranking and falls back to a generic set,
        # throwing away a near-perfect lexical match on
        # github_list_workflow_runs. An alias is what turns a strong
        # match into a CONFIDENT one.
        "ci",
        "build",
        "pipeline",
        "workflow",
        "action",
        "deployment",
        "release",
        "tag",
        "fork",
        "gist",
        "collaborator",
        "milestone",
    ),
    "google_drive": (
        "drive",
        "google drive",
        "gdrive",
        "file",
        "folder",
        "document",
        "doc",
        "spreadsheet",
        "sheet",
        "upload",
        "download",
        "storage",
        "attachment",
        "trash",
        "bin",
        "quota",
        "shortcut",
        "revision",
    ),
    "google_calendar": (
        "calendar",
        "google calendar",
        "gcal",
        "event",
        "meeting",
        "schedule",
        "appointment",
        "agenda",
        "availability",
        "free busy",
        "freebusy",
        "invite",
        "reminder",
        "booking",
        "rsvp",
        "invitation",
        "attendee",
        "standup",
        "timezone",
        # Time-of-day language. Calendar is the only service on this
        # server that is ABOUT time, so a sentence whose only signal
        # is "tomorrow" is a calendar sentence - and without an alias
        # it detects no service at all, scores zero confidence, and
        # falls back to a generic set. "Lunch with Sam tomorrow at
        # 1pm" was routed to GitHub for exactly that reason.
        "today",
        "tomorrow",
        "yesterday",
        "tonight",
        "next week",
        "this week",
    ),
    "slack": (
        "slack",
        "channel",
        "message",
        "dm",
        "chat",
        "thread",
        "conversation",
        "workspace",
        "ping",
        "discussion",
        "standup",
    ),
}


# ---------------------------------------------------------------------
# Per-tool keyword augmentation
# ---------------------------------------------------------------------
#
# Your tool docstrings are short — "List Slack channels." is four
# words. That is fine for an LLM reading a schema, but it starves a
# lexical router: there is almost no text to match against.
#
# Rather than rewrite 61 docstrings, we attach extra vocabulary here.
# Two reasons this is the better trade:
#
#   1. Docstrings are API documentation for the LLM. Stuffing them with
#      synonyms for the benefit of the router would degrade the thing
#      they are actually for.
#   2. Routing vocabulary is tuning data. It changes when you learn how
#      real users phrase things, which is far more often than the tool's
#      behaviour changes.
#
# Only ambiguous or vocabulary-poor tools need an entry.

TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    # --- GitHub ------------------------------------------------------
    "github_get_authenticated_user": (
        "whoami", "my account", "profile", "identity", "who am i",
    ),
    "github_list_commits": (
        "history", "changes", "log", "recent work", "activity",
    ),
    "github_get_file": (
        "source", "content", "read code", "view file",
    ),
    "github_search_code": (
        "function", "implementation", "where is", "grep", "find code",
    ),
    "github_search_issues": (
        "bug", "ticket", "report", "problem", "open issue",
    ),
    "github_list_issues": (
        "bug", "ticket", "open", "assigned", "backlog",
    ),
    "github_list_pull_requests": (
        "pr", "review", "merge request", "open pr", "pending review",
    ),
    "github_get_pull_request": (
        "pr", "review", "diff", "merge request",
    ),
    "github_add_issue_comment": (
        "reply", "respond", "comment on", "note",
    ),
    # The tool that actually FILES a bug had no entry, while three
    # tools that merely mention issues did. With a bigger GitHub
    # surface that asymmetry became a real mis-route: "create each
    # issue in the repo" ranked add_issue_labels above create_issue,
    # because "create" now appears in a dozen tool names and its IDF
    # collapsed, while "label" stayed rare and decisive.
    #
    # This is the lever the file's own header describes: an alias here
    # is worth more than any scoring change.
    "github_create_issue": (
        "file a bug", "raise a ticket", "open an issue", "new issue",
        "report a bug", "log a bug", "report", "raise",
    ),
    "github_create_repository": (
        "new repo", "new project", "start a repo", "init repository",
        "make a repo", "set up a repository",
    ),
    "github_delete_repository": (
        "remove repo", "destroy repository", "delete project",
    ),
    "github_create_branch": (
        "new branch", "cut a branch", "feature branch", "checkout",
    ),
    "github_update_file": (
        "commit", "commit a file", "write file", "push a change",
        "edit file", "save file", "create file", "add file",
    ),
    "github_delete_file": (
        "remove file", "delete from repo",
    ),
    "github_merge_pull_request": (
        "merge pr", "land", "ship", "merge it", "approve and merge",
    ),
    "github_create_pull_request_review": (
        "approve", "request changes", "review pr", "sign off",
        "lgtm",
    ),
    "github_list_pull_request_files": (
        "diff", "changed files", "what changed", "patch",
    ),
    "github_list_workflow_runs": (
        "ci", "build", "pipeline", "actions", "is ci passing",
        "failing build", "test run", "green", "red",
    ),
    "github_list_workflows": (
        "ci", "actions", "pipeline", "automation",
    ),
    "github_compare_commits": (
        "diff", "difference", "ahead", "behind", "what changed",
    ),
    "github_create_release": (
        "publish", "ship a version", "tag a release", "new version",
    ),
    "github_list_releases": (
        "version", "changelog", "tag", "published",
    ),
    "github_add_collaborator": (
        "grant access", "give access", "invite to repo", "add member",
        "share repository",
    ),
    "github_list_collaborators": (
        "who has access", "members", "team", "sharing",
    ),
    "github_list_notifications": (
        "inbox", "mentions", "unread", "what needs my attention",
    ),
    "github_create_gist": (
        "snippet", "paste", "share code", "pastebin",
    ),
    "github_get_readme": (
        "readme", "documentation", "what does this repo do",
    ),
    "github_list_labels": (
        "tag", "category", "label",
    ),
    # --- Google Drive ------------------------------------------------
    "google_drive_search_files": (
        "find", "locate", "look for", "document", "doc", "spreadsheet",
    ),
    "google_drive_read_file": (
        "content", "contents", "text", "open", "view", "what is in",
    ),
    "google_drive_list_folder": (
        "browse", "inside", "contents of", "what is in",
    ),
    "google_drive_list_permissions": (
        "sharing", "shared with", "access", "who can see",
    ),
    "google_drive_create_permission": (
        "share", "grant access", "give access", "invite to file",
    ),
    "google_drive_move_file": (
        "relocate", "put into", "organize",
    ),
    "google_drive_trash_file": (
        "bin", "recycle", "throw away", "get rid of", "remove file",
        "delete safely",
    ),
    "google_drive_restore_file": (
        "undelete", "recover", "bring back", "undo delete",
    ),
    "google_drive_empty_trash": (
        "empty bin", "purge", "clear out",
    ),
    "google_drive_copy_file": (
        "duplicate", "clone", "make a copy",
    ),
    "google_drive_export_file": (
        "convert", "save as", "pdf", "docx", "download a doc",
    ),
    "google_drive_get_storage_quota": (
        "space", "quota", "how full", "capacity", "usage",
        "running out",
    ),
    "google_drive_list_revisions": (
        "version", "history", "who changed", "previous version",
    ),
    "google_drive_update_permission": (
        "change access", "make editor", "make viewer", "role",
    ),
    "google_drive_delete_permission": (
        "revoke", "unshare", "remove access", "stop sharing",
    ),
    "google_drive_list_shared_drives": (
        "team drive", "shared drive", "workspace drive",
    ),
    # --- Google Calendar additions -----------------------------------
    "google_calendar_add_event": (
        "quick add", "add to calendar", "book", "schedule",
        "put in my calendar", "natural language",
    ),
    "google_calendar_search_events": (
        "find meeting", "look for event", "when was", "which meeting",
    ),
    "google_calendar_create_calendar": (
        "new calendar", "separate calendar", "another calendar",
        "start a calendar", "second calendar", "team calendar",
        "project calendar", "personal calendar", "work calendar",
    ),
    "google_calendar_delete_calendar": (
        "remove calendar", "destroy calendar",
    ),
    "google_calendar_clear_calendar": (
        # NOT "delete all events". That phrase shares both of its
        # content words with "delete the meeting tomorrow", and it
        # pushed this tool - which erases an entire calendar - above
        # google_calendar_delete_event for exactly that request.
        # A keyword list is routing vocabulary, and vocabulary that
        # overlaps a far narrower tool's is how an agent gets offered
        # the catastrophic version of what was asked for.
        "wipe", "empty calendar", "start the year again",
    ),
    "google_calendar_set_invitation_response": (
        "rsvp", "accept", "decline", "tentative", "going",
        "not going", "reply to invite",
    ),
    "google_calendar_move_event": (
        "transfer event", "other calendar",
    ),
    "google_calendar_list_event_instances": (
        "recurring", "repeating", "series", "occurrence",
        "single occurrence",
    ),
    "google_calendar_add_subscription": (
        "subscribe", "follow calendar", "add shared calendar",
    ),
    "google_calendar_remove_subscription": (
        "unsubscribe", "hide calendar", "stop following",
    ),
    # --- Google Calendar ---------------------------------------------
    "google_calendar_list_events": (
        "meeting", "agenda", "schedule", "today", "tomorrow",
        "upcoming", "appointment", "what do i have",
    ),
    "google_calendar_get_event": (
        "meeting detail", "appointment", "when is",
    ),
    "google_calendar_create_event": (
        "book", "schedule", "set up meeting", "add to calendar",
    ),
    # list_events and create_event both had entries and delete_event
    # did not, so "delete the meeting tomorrow" matched only the verb
    # - and the verb is deliberately down-weighted as grammar. The
    # tool that cancels ONE event needs at least the vocabulary of the
    # tools that list and create one, or the request finds the
    # calendar-wide tools instead.
    "google_calendar_delete_event": (
        "cancel", "meeting", "appointment", "call off",
        "remove from calendar", "drop the meeting",
    ),
    "google_calendar_freebusy": (
        "free", "busy", "availability", "available", "open slot",
        "conflict", "am i free",
    ),
    "google_calendar_list_acl": (
        "sharing", "access", "who can see", "permission",
    ),
    "google_calendar_create_acl_rule": (
        "share calendar", "grant access", "give access",
    ),
    # --- Slack -------------------------------------------------------
    "slack_auth_info": (
        "workspace", "identity", "whoami", "connection", "who am i",
    ),
    "slack_channel_history": (
        "conversation", "discussion", "discussed", "said", "talked",
        "recent", "transcript", "read messages", "past messages",
        "search messages", "find message",
    ),
    "slack_thread_replies": (
        "reply", "replies", "responses", "answers", "follow up",
    ),
    "slack_send_message": (
        "post", "notify", "tell", "announce", "dm", "write to",
        "summary", "update", "report to", "say", "share in channel",
        "let them know", "send to channel",
    ),
    "slack_list_users": (
        "member", "people", "person", "who", "teammate", "colleague",
    ),
    "slack_get_user": (
        "member", "person", "profile", "who is",
    ),
    "slack_list_channels": (
        "where", "which channel", "rooms",
    ),
    "slack_get_channel": (
        "channel info", "about this channel", "topic", "purpose",
    ),
    "slack_join_channel": (
        "join", "enter", "add myself", "become a member",
    ),
    "slack_open_conversation": (
        "dm", "direct message", "private message", "message someone",
        "start a chat", "pm",
    ),
    "slack_send_thread_reply": (
        "reply in thread", "answer", "respond in thread",
        "follow up",
    ),
    "slack_schedule_message": (
        "later", "send at", "remind", "post tomorrow", "delay",
    ),
    "slack_add_reaction": (
        "emoji", "react", "thumbs up", "tada", "acknowledge",
    ),
    "slack_pin_message": (
        "pin", "bookmark", "important message",
    ),
    "slack_search_messages": (
        "search", "find message", "look for", "who said",
    ),
    "slack_find_user_by_email": (
        "email", "look up person", "find person", "by address",
    ),
    "slack_add_channel_members": (
        "invite", "add people", "bring in", "grant access",
    ),
    "slack_get_permalink": (
        "link", "url", "permalink", "share a message",
    ),
    "slack_list_channel_members": (
        "who is in", "members of", "participants",
    ),
    "slack_get_team_info": (
        "workspace", "team", "organisation", "org",
    ),
}


# ---------------------------------------------------------------------
# Intent verbs
# ---------------------------------------------------------------------
#
# Maps the user's verb to the kind of operation they are asking for.
# Used to keep destructive tools away from read-shaped requests.

INTENT_VERBS: dict[Operation, tuple[str, ...]] = {
    Operation.READ: (
        "get", "list", "show", "find", "search", "read", "fetch",
        "view", "display", "check", "tell", "what", "which", "who",
        "when", "where", "download", "history", "retrieve", "look",
        "see", "summarize", "analyze", "review", "compare", "count",

        # Nouns that name a read as surely as any verb does. "repo
        # summary please" has no verb at all, so intent came out None,
        # every tool scored a neutral 0.5 on alignment - and
        # github_delete_repository was offered to a request that only
        # wanted to look at something.
        "summary", "summarise", "overview", "explain", "describe",
        "understand", "audit", "inspect", "explore", "browse",
    ),
    Operation.WRITE: (
        "create", "make", "add", "new", "send", "post", "write",
        "update", "edit", "modify", "change", "upload", "move",
        "rename", "comment", "reply", "invite", "schedule", "book",
        "open", "assign", "close", "merge", "set",
    ),
    Operation.DELETE: (
        "delete", "remove", "cancel", "drop", "clear", "purge",
        "archive", "erase", "discard",
    ),
    Operation.ADMIN: (
        "share", "unshare", "grant", "revoke", "permission",
        "access", "authorize",
    ),
}


# ---------------------------------------------------------------------
# Stopwords
# ---------------------------------------------------------------------
#
# Words that carry no routing signal. Removed before scoring so that
# "please can you show me my open github issues" is scored on
# {show, open, github, issue} rather than diluted across eleven tokens.
#
# IMPORTANT: intent verbs are deliberately absent from this list. Words
# like "show", "what" and "delete" look like filler but are exactly
# what tells us whether the user wants to read or destroy something.

STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "it", "its",
    "i", "me", "my", "mine", "we", "our", "you", "your", "they",
    "them", "their", "is", "are", "was", "were", "be", "been", "am",
    "do", "does", "did", "doing", "have", "has", "had", "will",
    "would", "should", "could", "can", "may", "might", "must",
    "to", "of", "in", "on", "at", "for", "from", "with", "about",
    "into", "over", "under", "by", "as", "and", "or", "but", "if",
    "then", "than", "so", "just", "also", "any", "some", "all",
    "please", "hey", "hi", "hello", "thanks", "thank", "ok", "okay",
    "there", "here", "up", "out", "down", "off", "again", "very",
    "s", "t", "re", "ve", "ll", "d", "m",

    # Function words that were missing, and were not harmless.
    #
    # A grammatical word still gets compared against tool vocabulary,
    # and fuzzy_ratio's PREFIX rule scores a three-letter prefix at
    # 0.94 - all but an exact match. So on a real request ending
    # "Do not modify code... Use minimum tokens and avoid unnecessary
    # exploration", the boilerplate scored higher than the request:
    #
    #     "use" -> "user"     0.94   lifted slack_get_user_profile
    #     "not" -> "notify"   0.94   lifted anything with a keyword
    #
    # and slack_send_message - the tool the sentence was actually
    # asking for - was left out of the routed set entirely.
    #
    # Every word here is grammar. None is an intent verb; that
    # exclusion is the rule this list must never break, and it is
    # checked by tests/router/test_router.py.
    "not", "no", "nor", "use", "uses", "using", "used",
    "each", "every", "both", "either", "neither",
    "how", "why", "after", "before", "while",
    "only", "own", "same", "other", "such", "even", "ever", "never",

    # THE AFFIRMATIVES. The same bug as "use" -> "user" above, on the
    # single most common follow-up word in the language - and "no" was
    # already here while "yes" was not.
    #
    #     "yes" -> "yesterday"   0.94   a Google Calendar alias
    #
    # From a real session. The user listed their GitHub repositories,
    # asked to delete one, was asked to confirm, and typed "Yes, delete
    # it". That message scored google_calendar at 0.940 - on the
    # strength of "yes" - and scored nothing else. GitHub was not
    # offered at all, so the agent reported that it had no GitHub tools
    # and had "made up" the repository list it had genuinely fetched two
    # messages earlier.
    #
    # Worse, a namespace HAD scored, so the previous-turn carry-over in
    # router.route never fired: that path only runs when the message
    # names no service, and this message named the wrong one confidently.
    #
    # A confirmation carries no topic by definition - it inherits the
    # topic of the question it answers. None of these is an intent verb,
    # which is the rule this list must never break.
    "yes", "yeah", "yep", "yup", "yah", "ya",
    "sure", "agreed", "absolutely", "definitely", "affirmative",
})


# Flattened view of every intent verb, for fast membership tests.
ALL_INTENT_VERBS: frozenset[str] = frozenset(
    verb
    for verbs in INTENT_VERBS.values()
    for verb in verbs
)


# ---------------------------------------------------------------------
# Task expansions
# ---------------------------------------------------------------------
#
# Some words name a PLAN, not a tool.
#
# "Summarise this repository" is the clearest example, and it defeated
# the router completely. Nothing in the request resembles the name or
# the description of any tool: no tool is called `summarize`, and no
# docstring contains the word. So the only token with any weight was
# "repo", which matches eleven GitHub tools EQUALLY - every one of them
# named after the repository resource:
#
#     github_list_repositories      lexical 0.188
#     github_get_repository         lexical 0.188
#     github_create_repository      lexical 0.188
#     github_delete_repository      lexical 0.188      <- read request
#     ...eleven of them, tied to three decimal places
#
# When every candidate ties, the ranking is decided by whatever noise
# is left over - and the tools that actually answer the question
# (`github_get_readme`, `github_get_file`) sat at rank 12 and 14, just
# past a top_k of 8. The model was handed eight ways to manage
# repositories and no way to read one, so it did what the transcript
# shows: hedged, guessed the tech stack from the repository's NAME,
# and told the user it had no tool for the job.
#
# No amount of weight tuning fixes that, because the missing link is
# not lexical. "Summary" and "readme" have nothing in common as
# strings; they are related because of what a summary is MADE OF. That
# knowledge lives in the head of whoever built the service, so it gets
# written down here.
#
# HOW THIS RELATES TO THE OTHER TWO FIXES FOR THE SAME QUERY
#
#   embeddings          learn some of this from text, imperfectly and
#                       at the cost of a network call. Complementary:
#                       they cover words nobody thought to list here.
#   find_tools          lets the model ask mid-turn once it has seen
#                       the repository. Complementary: it covers the
#                       second step, which no first-shot router can
#                       know about.
#
# This table is the cheapest of the three and the only deterministic
# one. Nine lines of data beat a model round-trip when the mapping is
# something you already know.
#
# Terms are matched against the INDEX vocabulary like any other query
# token, so a term no tool uses simply scores nothing. That is what
# keeps this table safe to extend: it can be wrong, but it cannot be
# harmful.

TASK_EXPANSIONS: dict[str, tuple[str, ...]] = {

    # "Tell me what is in this thing" - answered by reading its
    # contents, whatever "contents" means for that service.
    "summary": ("readme", "file", "content", "description", "history"),
    "summarize": ("readme", "file", "content", "description", "history"),
    "summarise": ("readme", "file", "content", "description", "history"),
    "overview": ("readme", "file", "content", "description", "history"),
    "explain": ("readme", "file", "content", "description"),
    "describe": ("readme", "file", "content", "description"),
    "understand": ("readme", "file", "content", "description"),
    "analyze": ("readme", "file", "content", "commit", "history"),
    "audit": ("file", "content", "commit", "history", "permission"),
    "review": ("file", "content", "commit", "diff", "pull"),

    # "What is this built with" - the answer is in the files.
    "stack": ("readme", "file", "content", "language"),
    "tech": ("readme", "file", "content", "language"),
    "framework": ("readme", "file", "content", "language"),
    "dependency": ("file", "content", "readme"),

    # Structure questions are directory listings.
    "structure": ("file", "content", "tree", "directory", "folder"),
    "layout": ("file", "content", "tree", "directory", "folder"),
    "documentation": ("readme", "file", "content", "document"),
    "docs": ("readme", "file", "content", "document"),

    # Activity questions are history questions.
    "activity": ("commit", "history", "event", "message"),
    "recent": ("commit", "history", "event", "message"),
    "progress": ("commit", "history", "issue", "pull"),
}


def expansions_for_task(token: str) -> tuple[str, ...]:
    """
    Extra search terms for a task word, or empty if it is not one.

    Exact lookup only. Typo tolerance is applied by the CALLER, which
    already owns the fuzzy machinery and the thresholds - duplicating
    it here would give the project two definitions of "close enough".
    """

    return TASK_EXPANSIONS.get(token, ())


def all_namespace_aliases() -> dict[str, tuple[str, ...]]:
    """
    Accessor so callers do not import the dict directly.

    A function is a seam: when these aliases eventually come from the
    database (per-tenant vocabulary, learned synonyms), only this body
    changes and no caller notices.
    """

    return NAMESPACE_ALIASES


def keywords_for_tool(tool_name: str) -> tuple[str, ...]:
    """
    Extra search vocabulary for one tool, or empty if none is defined.
    """

    return TOOL_KEYWORDS.get(tool_name, ())
