from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from api.credentials import FernetCredentialStore  # noqa: E402
from api.db.models import OAuthState, PluginConnection, User  # noqa: E402
from api.oauth.base import TokenSet  # noqa: E402
from api.services import oauth_service  # noqa: E402
from api.services.oauth_service import InvalidState  # noqa: E402

"""
The security of the OAuth flow, which is entirely in the state row.

Every test here is about a callback that must NOT be honoured. The
happy path is one test; the other seven are the reasons this is not a
two-line function.

The provider is faked - no network. That is not a shortcut: the parts
worth testing are the ones we wrote, and a real consent screen cannot
be automated anyway.
"""


CRYPTO_KEY = "8ZzQKQzRHJ1t0m4xkG_p2dxKlPzKQ0RvKUOaKu5Tqbg="

STORE = FernetCredentialStore(CRYPTO_KEY)


class FakeSettings:
    """Only the fields oauth_service reads."""

    oauth_redirect_base = "https://app.test"
    frontend_base_url = "https://app.test"
    oauth_state_ttl_seconds = 600
    oauth_refresh_skew_seconds = 300

    github_client_id = "cid"
    google_client_id = "cid"
    slack_client_id = "cid"

    class _Secret:
        @staticmethod
        def get_secret_value() -> str:
            return "shhh"

    github_client_secret = _Secret()
    google_client_secret = _Secret()
    slack_client_secret = _Secret()


SETTINGS = FakeSettings()


class FakeProvider:
    """A provider that answers without a network."""

    key = "github"
    label = "GitHub"
    scopes = ("repo", "read:user")
    uses_pkce = False

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.exchanges: list[tuple[str, str | None]] = []

    def authorize_url(self, *, state: str, verifier: str | None) -> str:
        return f"https://provider.test/authorize?state={state}"

    async def exchange(self, *, code: str, verifier: str | None) -> TokenSet:
        self.exchanges.append((code, verifier))

        if self.fail:
            from api.oauth.base import OAuthError

            raise OAuthError("bad code")

        return TokenSet(
            access_token="ghp_realtoken",
            refresh_token="refresh-1",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=("repo", "read:user"),
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        return TokenSet(
            access_token="ghp_refreshed",
            refresh_token=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=("repo",),
        )

    async def identity(self, access_token: str) -> str | None:
        return "octocat"


def _use_fake(monkey: FakeProvider):
    """Swap the registry lookup for the fake, in both modules."""

    oauth_service.build_provider = lambda settings, key: monkey  # type: ignore


def _restore():
    from api.oauth import build_provider

    oauth_service.build_provider = build_provider  # type: ignore


def _database_url() -> str | None:
    try:
        from api.settings import get_settings

        return get_settings().database_url

    except Exception:
        return None


def run_committed(body) -> bool:
    url = _database_url()

    if not url:
        return False

    async def main() -> bool:
        engine = create_async_engine(url)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with engine.connect():
                pass

        except Exception:
            await engine.dispose()
            return False

        created: list[uuid.UUID] = []

        try:
            await body(maker, created)

        finally:
            _restore()

            async with maker() as db:
                for user_id in created:
                    await db.execute(delete(User).where(User.id == user_id))
                await db.commit()

            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


async def _make_user(maker, created) -> uuid.UUID:
    async with maker() as db:
        user = User(
            email=f"oauth-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="not-a-real-hash",
        )
        db.add(user)
        await db.commit()

        created.append(user.id)

        return user.id


# ---------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------


def test_a_complete_flow_stores_an_encrypted_connection():

    async def body(maker, created):

        user_id = await _make_user(maker, created)

        provider = FakeProvider()
        _use_fake(provider)

        async with maker() as db:

            url = await oauth_service.start(
                db, SETTINGS, user_id, "github"
            )
            await db.commit()

            assert "state=" in url

            state = url.split("state=")[1]

            connection, redirect_to = await oauth_service.complete(
                db, SETTINGS, STORE, "github",
                state=state, code="the-code",
            )
            await db.commit()

        assert connection.status == "connected"
        assert connection.account_label == "octocat"

        # What the provider GRANTED, which can be less than what was
        # asked for.
        assert connection.scopes == ["repo", "read:user"]

        # THE MILESTONE ASSERTION: ciphertext, never the token.
        blob = bytes(connection.credentials_enc)

        assert b"ghp_realtoken" not in blob
        assert b"refresh-1" not in blob

        # ...and it round-trips for the code that is allowed to read it.
        assert STORE.decrypt(blob)["access_token"] == "ghp_realtoken"

    if not run_committed(body):
        _skipped("test_a_complete_flow_stores_an_encrypted_connection")


# ---------------------------------------------------------------------
# Callbacks that must be refused
# ---------------------------------------------------------------------


def test_a_callback_with_no_state_is_refused():

    async def body(maker, created):
        _use_fake(FakeProvider())

        async with maker() as db:
            for state, code in ((None, "c"), ("s", None), ("", "")):
                try:
                    await oauth_service.complete(
                        db, SETTINGS, STORE, "github",
                        state=state, code=code,
                    )
                    raise AssertionError("a bare callback was accepted")

                except InvalidState:
                    pass

    if not run_committed(body):
        _skipped("test_a_callback_with_no_state_is_refused")


def test_an_unknown_state_is_refused():
    """
    The CSRF test.

    Without this check anyone can forge a callback carrying THEIR
    authorization code, and the victim's account silently gets
    connected to the attacker's GitHub - every issue the agent later
    reads comes from the attacker's repositories.
    """

    async def body(maker, created):
        _use_fake(FakeProvider())

        async with maker() as db:
            try:
                await oauth_service.complete(
                    db, SETTINGS, STORE, "github",
                    state="not-a-state-we-issued",
                    code="attacker-code",
                )
                raise AssertionError("a forged state was accepted")

            except InvalidState:
                pass

    if not run_committed(body):
        _skipped("test_an_unknown_state_is_refused")


def test_a_state_cannot_be_used_twice():
    """The replay test - this is why state is a ROW, not a signed token."""

    async def body(maker, created):

        user_id = await _make_user(maker, created)

        provider = FakeProvider()
        _use_fake(provider)

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "github")
            await db.commit()

            state = url.split("state=")[1]

            await oauth_service.complete(
                db, SETTINGS, STORE, "github", state=state, code="c1"
            )
            await db.commit()

            try:
                await oauth_service.complete(
                    db, SETTINGS, STORE, "github", state=state, code="c2"
                )
                raise AssertionError("a state was redeemed twice")

            except InvalidState:
                pass

        # The second code never reached the provider.
        assert provider.exchanges == [("c1", None)]

    if not run_committed(body):
        _skipped("test_a_state_cannot_be_used_twice")


def test_an_expired_state_is_refused():

    async def body(maker, created):

        user_id = await _make_user(maker, created)
        _use_fake(FakeProvider())

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "github")
            state = url.split("state=")[1]

            row = await db.scalar(
                select(OAuthState).where(OAuthState.state == state)
            )
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()

            try:
                await oauth_service.complete(
                    db, SETTINGS, STORE, "github", state=state, code="c"
                )
                raise AssertionError("an expired state was accepted")

            except InvalidState:
                pass

    if not run_committed(body):
        _skipped("test_an_expired_state_is_refused")


def test_a_state_minted_for_one_service_cannot_connect_another():
    """
    Without the plugin_key check the user_id is still correct - but the
    ATTACKER chooses which of their accounts gets attached, by starting
    a Slack flow and redeeming the state on the GitHub callback.
    """

    async def body(maker, created):

        user_id = await _make_user(maker, created)
        _use_fake(FakeProvider())

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "slack")
            await db.commit()

            state = url.split("state=")[1]

            try:
                await oauth_service.complete(
                    db, SETTINGS, STORE, "github", state=state, code="c"
                )
                raise AssertionError("a state crossed services")

            except InvalidState:
                pass

    if not run_committed(body):
        _skipped("test_a_state_minted_for_one_service_cannot_connect_another")


def test_a_failed_exchange_still_burns_the_state():
    """
    The state is marked used BEFORE the exchange.

    If a failed exchange left it usable, a replayed callback would get
    another attempt - which is exactly the window a stolen code needs.
    """

    async def body(maker, created):

        user_id = await _make_user(maker, created)
        _use_fake(FakeProvider(fail=True))

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "github")
            await db.commit()

            state = url.split("state=")[1]

            try:
                await oauth_service.complete(
                    db, SETTINGS, STORE, "github", state=state, code="c"
                )
                raise AssertionError("a failing exchange reported success")

            except Exception as exc:
                assert not isinstance(exc, AssertionError)

            await db.commit()

            row = await db.scalar(
                select(OAuthState).where(OAuthState.state == state)
            )

            assert row.used_at is not None

    if not run_committed(body):
        _skipped("test_a_failed_exchange_still_burns_the_state")


def test_the_connection_lands_on_the_user_who_started_the_flow():
    """
    The callback carries no Authorization header - a browser following
    a 302 does not send one. The identity comes from the state row, and
    this proves it lands on the right tenant.
    """

    async def body(maker, created):

        alice = await _make_user(maker, created)
        bob = await _make_user(maker, created)

        _use_fake(FakeProvider())

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, alice, "github")
            await db.commit()

            state = url.split("state=")[1]

            connection, _ = await oauth_service.complete(
                db, SETTINGS, STORE, "github", state=state, code="c"
            )
            await db.commit()

            assert connection.user_id == alice

            others = list(
                await db.scalars(
                    select(PluginConnection).where(
                        PluginConnection.user_id == bob
                    )
                )
            )

            assert others == []

    if not run_committed(body):
        _skipped("test_the_connection_lands_on_the_user_who_started_the_flow")


# ---------------------------------------------------------------------
# Open redirect
# ---------------------------------------------------------------------


def test_redirect_to_must_be_a_path_on_our_own_frontend():
    """
    An unchecked redirect_to is an open redirect: an attacker sends a
    link that starts a GENUINE OAuth flow on your domain and lands the
    victim on a page that is not yours, with your address bar showing
    the whole way.
    """

    safe = oauth_service._safe_redirect(SETTINGS, "/plugins?tab=connected")

    assert safe == "/plugins?tab=connected"

    for hostile in (
        "https://evil.test/steal",
        "//evil.test/steal",
        "http://app.test.evil.test",
        "javascript:alert(1)",
    ):
        assert oauth_service._safe_redirect(SETTINGS, hostile) is None


# ---------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------


def test_a_token_that_is_not_near_expiry_is_not_refreshed():

    async def body(maker, created):

        user_id = await _make_user(maker, created)

        provider = FakeProvider()
        _use_fake(provider)

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "github")
            await db.commit()

            state = url.split("state=")[1]

            connection, _ = await oauth_service.complete(
                db, SETTINGS, STORE, "github", state=state, code="c"
            )

            # An hour of life left; the skew is five minutes.
            connection.expires_at = datetime.now(timezone.utc) + timedelta(
                hours=1
            )
            await db.commit()

            token = await oauth_service.access_token(
                db, SETTINGS, STORE, user_id, "github"
            )
            await db.commit()

            assert token == "ghp_realtoken"

    if not run_committed(body):
        _skipped("test_a_token_that_is_not_near_expiry_is_not_refreshed")


def test_a_token_inside_the_skew_is_refreshed_before_it_fails():
    """
    REFRESH BEFORE EXPIRY, NOT AFTER.

        bad   call -> 401 -> refresh -> retry   (visible failure hourly)
        good  before use: expiring soon? refresh (invisible)
    """

    async def body(maker, created):

        user_id = await _make_user(maker, created)
        _use_fake(FakeProvider())

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "github")
            await db.commit()

            state = url.split("state=")[1]

            connection, _ = await oauth_service.complete(
                db, SETTINGS, STORE, "github", state=state, code="c"
            )

            # Still valid - but only for another minute, and the skew
            # is five. A token that passes the check and then expires
            # mid-request puts you on the bad path anyway.
            connection.expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=60
            )
            await db.commit()

            token = await oauth_service.access_token(
                db, SETTINGS, STORE, user_id, "github"
            )
            await db.commit()

            assert token == "ghp_refreshed"

            refreshed = await db.get(PluginConnection, connection.id)
            payload = STORE.decrypt(bytes(refreshed.credentials_enc))

            # The refresh token SURVIVES. A refresh response does not
            # repeat it, and losing it here would leave the connection
            # able to refresh exactly once and then expire for good.
            assert payload["refresh_token"] == "refresh-1"

    if not run_committed(body):
        _skipped("test_a_token_inside_the_skew_is_refreshed_before_it_fails")


def test_a_failed_refresh_marks_the_connection_expired():

    async def body(maker, created):

        user_id = await _make_user(maker, created)

        class Broken(FakeProvider):
            async def refresh(self, refresh_token: str):
                from api.oauth.base import OAuthError

                raise OAuthError("refresh token revoked")

        _use_fake(Broken())

        async with maker() as db:

            url = await oauth_service.start(db, SETTINGS, user_id, "github")
            await db.commit()

            state = url.split("state=")[1]

            connection, _ = await oauth_service.complete(
                db, SETTINGS, STORE, "github", state=state, code="c"
            )
            connection.expires_at = datetime.now(timezone.utc)
            await db.commit()

            token = await oauth_service.access_token(
                db, SETTINGS, STORE, user_id, "github"
            )
            await db.commit()

            assert token is None

            refreshed = await db.get(PluginConnection, connection.id)

            # ONLY a failed REFRESH sets this. An expired access token
            # is normal and invisible; an expired refresh token means
            # the user must reconnect, and the UI can now say so.
            assert refreshed.status == "expired"

    if not run_committed(body):
        _skipped("test_a_failed_refresh_marks_the_connection_expired")


def test_expired_states_are_purged():

    async def body(maker, created):

        user_id = await _make_user(maker, created)
        _use_fake(FakeProvider())

        async with maker() as db:

            await oauth_service.start(db, SETTINGS, user_id, "github")
            await db.commit()

            # Everything issued so far is now in the past.
            purged = await oauth_service.purge_expired_states(
                db,
                before=datetime.now(timezone.utc) + timedelta(days=1),
            )
            await db.commit()

            assert purged >= 1

    if not run_committed(body):
        _skipped("test_expired_states_are_purged")
