from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.tenancy as tenancy  # noqa: E402
from core.tenancy import (  # noqa: E402
    CredentialMiddleware,
    MissingCredential,
    ServiceProxy,
    credential_for,
    env_credentials_allowed,
    reset_cache,
    service_proxy,
)

"""
Cross-tenant isolation inside the MCP server.

Phase5.md calls these mandatory, and it is right: a cross-tenant leak
is the one bug that ends a client relationship, and it is invisible
until somebody sees another company's repositories.

Every test here is about the same question asked differently: can one
request end up using another request's credentials?
"""


class FakeService:
    """Records which token it was built with."""

    def __init__(self, token):
        self.token = token
        self.closed = False

    def whoami(self):
        return self.token

    def close(self):
        self.closed = True


def _proxy() -> ServiceProxy:
    return service_proxy("github", lambda token: FakeService(token))


class FakeCtx:
    def __init__(self, meta):
        self.meta = meta


async def _call(middleware, meta, body):
    """Run `body` the way a tool runs: inside the middleware chain."""

    async def call_next(ctx):
        return body()

    return await middleware(FakeCtx(meta), call_next)


def run(coro):
    return asyncio.run(coro)


def setup_function():
    # pytest calls this; tests/run_tests.py does not, so every test
    # that inspects the cache resets it ITSELF as well. A test that
    # only passes under one runner is a test that will be believed
    # under the other.
    reset_cache()


# ---------------------------------------------------------------------
# The middleware
# ---------------------------------------------------------------------


def test_credentials_from_metadata_reach_the_service():

    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        return await _call(
            middleware,
            {"credentials": {"github": "alice-token"}},
            lambda: github.whoami(),
        )

    assert run(body()) == "alice-token"


def test_two_users_get_two_services():

    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        alice = await _call(
            middleware,
            {"credentials": {"github": "alice-token"}},
            lambda: github.whoami(),
        )

        bob = await _call(
            middleware,
            {"credentials": {"github": "bob-token"}},
            lambda: github.whoami(),
        )

        return alice, bob

    alice, bob = run(body())

    # THE test. Two requests, two accounts, in one process that used to
    # hold exactly one token.
    assert alice == "alice-token"
    assert bob == "bob-token"


def test_a_request_does_not_inherit_the_previous_requests_credentials():
    """
    The leak that would be silent.

    ContextVars are per-task, and each MCP request is handled in its
    own context - so a request that sends nothing sees the DEFAULT, not
    whatever the last caller set. A module-level global would fail this
    test and nothing else.
    """

    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        await _call(
            middleware,
            {"credentials": {"github": "alice-token"}},
            lambda: github.whoami(),
        )

        # A second request with no credentials at all.
        return await _call(middleware, {}, lambda: credential_for("github"))

    assert run(body()) is None


def test_credentials_do_not_cross_services():

    async def body():
        middleware = CredentialMiddleware()

        return await _call(
            middleware,
            {"credentials": {"github": "gh", "slack": "sl"}},
            lambda: (
                credential_for("github"),
                credential_for("slack"),
                credential_for("google_drive"),
            ),
        )

    github, slack, drive = run(body())

    assert github == "gh"
    assert slack == "sl"

    # Never connected, so never guessed at.
    assert drive is None


def test_the_user_id_travels_but_is_not_a_credential():

    async def body():
        middleware = CredentialMiddleware()

        return await _call(
            middleware,
            {"user_id": "usr_1", "credentials": {"github": "t"}},
            lambda: (tenancy.current_user_id(), credential_for("github")),
        )

    user_id, token = run(body())

    assert user_id == "usr_1"
    assert token == "t"


def test_empty_and_missing_metadata_are_harmless():
    # Every call from the CLI and the test suite looks like this.

    async def body():
        middleware = CredentialMiddleware()

        for meta in ({}, {"credentials": {}}, {"credentials": None}, None):
            got = await _call(middleware, meta, lambda: credential_for("github"))

            if got is not None:
                return got

        return None

    assert run(body()) is None


# ---------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------


def test_in_production_a_missing_credential_is_refused():
    """
    The back door this phase exists to close.

    In development a request with no credentials falls back to .env,
    which keeps the CLI working. In production that fallback would hand
    the OPERATOR's GitHub account to a user who never connected one -
    the original bug, reintroduced silently.
    """

    previous = os.environ.get("MCP_ENVIRONMENT")

    os.environ["MCP_ENVIRONMENT"] = "production"
    os.environ.pop("MCP_ALLOW_ENV_CREDENTIALS", None)

    try:
        assert env_credentials_allowed() is False

        github = _proxy()

        try:
            github.whoami()
            raise AssertionError("a credential-less call was served")

        except MissingCredential:
            pass

    finally:
        if previous is None:
            os.environ.pop("MCP_ENVIRONMENT", None)
        else:
            os.environ["MCP_ENVIRONMENT"] = previous


def test_a_users_request_never_falls_back_to_the_environment():
    """
    The leak that survived "development mode".

    MCP_ENVIRONMENT=development switches the .env fallback ON, and that
    is correct for the CLI. It was NOT correct for a real user whose
    token failed to resolve: their request carried no credential, fell
    through to .env, and was served from the OPERATOR's GitHub account.
    Nothing in the UI said so - the reads simply returned somebody
    else's repositories.

    The user id is what tells the two apart. The backend sets it on
    every call it makes on somebody's behalf; the CLI sets nothing. So
    a request that names a user must fail closed even here.
    """

    previous = os.environ.get("MCP_ENVIRONMENT")

    os.environ["MCP_ENVIRONMENT"] = "development"

    async def body():
        middleware = CredentialMiddleware()

        # A real user's request: identified, but with no github
        # credential - not connected, or the token would not decrypt.
        return await _call(
            middleware,
            {"user_id": "usr_1"},
            lambda: _proxy().whoami(),
        )

    try:
        assert env_credentials_allowed() is True

        try:
            run(body())
            raise AssertionError(
                "a user's request was served from the operator's .env"
            )

        except MissingCredential:
            # Fail CLOSED. An error the user can act on - "connect
            # GitHub" - beats silently browsing somebody else's
            # account, which looks exactly like success.
            pass

    finally:
        if previous is None:
            os.environ.pop("MCP_ENVIRONMENT", None)
        else:
            os.environ["MCP_ENVIRONMENT"] = previous

        reset_cache()


def test_development_still_falls_back_to_the_environment():
    previous = os.environ.get("MCP_ENVIRONMENT")

    os.environ["MCP_ENVIRONMENT"] = "development"

    try:
        assert env_credentials_allowed() is True

        # No credentials in context: the factory is called with None,
        # which is what makes the service read .env.
        assert _proxy().whoami() is None

    finally:
        if previous is None:
            os.environ.pop("MCP_ENVIRONMENT", None)
        else:
            os.environ["MCP_ENVIRONMENT"] = previous


def test_the_fallback_can_be_forced_on_in_production():
    # An explicit escape hatch, because a self-hosted single-user
    # deployment is a real thing and should not need a fake environment
    # name to work.
    previous_env = os.environ.get("MCP_ENVIRONMENT")

    os.environ["MCP_ENVIRONMENT"] = "production"
    os.environ["MCP_ALLOW_ENV_CREDENTIALS"] = "true"

    try:
        assert env_credentials_allowed() is True

    finally:
        os.environ.pop("MCP_ALLOW_ENV_CREDENTIALS", None)

        if previous_env is None:
            os.environ.pop("MCP_ENVIRONMENT", None)
        else:
            os.environ["MCP_ENVIRONMENT"] = previous_env


# ---------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------


def test_the_same_token_reuses_one_service():
    reset_cache()

    # A user's second tool call in a turn must reuse the first call's
    # HTTP connection pool. Building a service per call throws away
    # every keep-alive connection.
    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        first = await _call(
            middleware,
            {"credentials": {"github": "same"}},
            lambda: github._resolved_marker(),
        )

        return first

    # A marker the FakeService does not define would raise, so instead
    # compare identity through two resolutions.
    reset_cache()

    async def identity():
        middleware = CredentialMiddleware()
        github = _proxy()

        ids = []

        for _ in range(2):
            ids.append(
                await _call(
                    middleware,
                    {"credentials": {"github": "same"}},
                    lambda: id(github.whoami) and github.whoami(),
                )
            )

        return ids

    assert run(identity()) == ["same", "same"]

    # One entry, not two.
    assert len(tenancy._cache) == 1


def test_different_tokens_are_never_shared():
    reset_cache()

    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        for token in ("a", "b", "c"):
            await _call(
                middleware,
                {"credentials": {"github": token}},
                lambda: github.whoami(),
            )

    run(body())

    # Three tokens, three cached services. A single shared entry here
    # would BE the cross-tenant bug.
    assert len(tenancy._cache) == 3


def test_the_cache_is_keyed_by_a_hash_not_the_token():
    reset_cache()

    # A dict keyed by live credentials shows every one of them in a
    # debugger, a heap dump, or a stray repr.
    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        await _call(
            middleware,
            {"credentials": {"github": "ghp_verysecret"}},
            lambda: github.whoami(),
        )

    run(body())

    for namespace, fingerprint in tenancy._cache:
        assert "ghp_verysecret" not in fingerprint


def test_the_cache_is_bounded():
    reset_cache()

    async def body():
        middleware = CredentialMiddleware()
        github = _proxy()

        for i in range(tenancy.CACHE_MAX_ENTRIES + 10):
            await _call(
                middleware,
                {"credentials": {"github": f"token-{i}"}},
                lambda: github.whoami(),
            )

    run(body())

    # Otherwise it is one connection pool per user who ever logged in.
    assert len(tenancy._cache) <= tenancy.CACHE_MAX_ENTRIES


def test_the_proxy_never_renders_a_service_or_a_token():
    # repr() ends up in tracebacks and log lines.
    assert repr(_proxy()) == "<ServiceProxy github>"
