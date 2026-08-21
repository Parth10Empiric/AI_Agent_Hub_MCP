from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.mcp.credentials import CredentialResolver, build_resolver  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
The backend half: which credential is sent for which tool.

No database - `oauth_service.access_token` is replaced, because what is
under test is the ROUTING of credentials, not the OAuth refresh (which
tests/oauth already covers).
"""


REGISTRY = build_registry()


class FakeEngine:
    def __init__(self) -> None:
        self.registry = REGISTRY

    def get_tool(self, name):
        return self.registry.get(name)


ENGINE = FakeEngine()
USER = uuid.uuid4()


class ScriptedResolver(CredentialResolver):
    """
    A resolver whose token lookup is scripted.

    A SUBCLASS, not a monkeypatched attribute: CredentialResolver uses
    __slots__, so an instance refuses new attributes - which is the
    class doing its job. Overriding in a subclass is the honest way to
    substitute behaviour, and it keeps the production class closed.
    """

    def __init__(self, tokens, calls=None):
        super().__init__(
            session=None,
            engine=ENGINE,
            settings=object(),
            store=object(),
            user_id=USER,
        )

        # __slots__ on the base means these live on the subclass, which
        # declares none - so it has a __dict__ and this works.
        self.tokens = tokens
        self.calls = calls

    async def _token_for(self, namespace):
        if self.calls is not None:
            self.calls.append(namespace)

        return self.tokens.get(namespace)


def _resolver(tokens: dict[str, str], calls: list[str] | None = None):
    return ScriptedResolver(tokens, calls)


def run(coro):
    return asyncio.run(coro)


def test_the_meta_carries_only_the_service_the_tool_needs():
    calls: list[str] = []

    resolver = _resolver(
        {"github": "gh", "slack": "sl", "google_drive": "gd"},
        calls,
    )

    meta = run(resolver.meta_for("github_list_issues"))

    assert meta["credentials"] == {"github": "gh"}

    # ONE lookup, not four. A GitHub call must not cause the user's
    # Drive and Slack tokens to be decrypted and shipped across a pipe
    # - each of those is a chance to leak something the call never
    # needed.
    assert calls == ["github"]


def test_the_namespace_comes_from_the_tool_definition():
    # Not from parsing the name. A prefix check would break the first
    # time a tool is renamed, and would break silently.
    resolver = _resolver({"google_calendar": "cal"})

    meta = run(resolver.meta_for("google_calendar_list_events"))

    assert meta["credentials"] == {"google_calendar": "cal"}


def test_the_user_id_is_always_sent():
    resolver = _resolver({})

    meta = run(resolver.meta_for("github_list_issues"))

    # So a log line on the MCP side can say whose call it was, with the
    # token nowhere near it.
    assert meta["user_id"] == str(USER)


def test_no_connection_means_no_credentials_key():
    resolver = _resolver({})

    meta = run(resolver.meta_for("github_list_issues"))

    # NOT an empty string, and not the operator's token. What happens
    # next is the MCP server's decision - .env in development, a
    # refusal in production - so the rule lives in exactly one place.
    assert "credentials" not in meta


def test_an_unknown_tool_gets_no_credentials():
    resolver = _resolver({"github": "gh"})

    meta = run(resolver.meta_for("github_does_not_exist"))

    assert "credentials" not in meta


def test_a_failing_lookup_degrades_to_no_credentials():
    """
    A broken database must not take the turn down with it.

    The real class catches inside _token_for, so meta_for never raises
    into the executor - a tool call should fail as a tool call, not as
    a 500 halfway through a turn that has already done work.
    """

    resolver = CredentialResolver(
        session=None,
        engine=ENGINE,
        settings=object(),
        store=None,          # makes access_token blow up on use
        user_id=USER,
    )

    meta = run(resolver.meta_for("github_list_issues"))

    assert "credentials" not in meta
    assert meta["user_id"] == str(USER)


def test_no_store_means_no_resolver():
    # The CLI, the tests, a scheduled script. They call the same code
    # path and simply send nothing - which is why this whole phase is
    # invisible to them.
    assert build_resolver(None, ENGINE, None, None, USER) is None
    assert build_resolver(None, ENGINE, object(), None, USER) is None
    assert build_resolver(None, ENGINE, None, object(), USER) is None

    assert build_resolver(None, ENGINE, object(), object(), USER) is not None
