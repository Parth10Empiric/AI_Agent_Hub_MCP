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
})


# Flattened view of every intent verb, for fast membership tests.
ALL_INTENT_VERBS: frozenset[str] = frozenset(
    verb
    for verbs in INTENT_VERBS.values()
    for verb in verbs
)


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
