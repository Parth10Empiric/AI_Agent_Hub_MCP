from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from api.db.models import PluginConnection, User  # noqa: E402
from api.services import plugin_service  # noqa: E402
from api.verification import (  # noqa: E402
    CredentialRejected,
    VerificationUnavailable,
    VerifiedCredential,
)
from tests.tool_fixtures import build_registry  # noqa: E402

"""
Nothing is written until the service confirms the credential.

Verifying and then storing anyway would be a subtler version of the
same bug - so this checks the DATABASE, not the return value. The
question is not "did connect() raise", it is "is there a row claiming
a connection that does not exist".

HOW THESE RUN WITHOUT A THROWAWAY DATABASE

Same pattern as tests/permissions/test_isolation.py: one connection,
one transaction, rolled back at the end. The service layer only
flushes - the commit lives in api/db/session.get_db - so nothing here
can reach the developer's database permanently.
"""


class FakeEngine:
    """Just the registry, which is all `connect` looks at."""

    def __init__(self) -> None:
        self.registry = build_registry()


class FakeStore:
    """Encryption is not what these tests are about."""

    @property
    def version(self) -> int:
        return 1

    def encrypt(self, payload: dict) -> bytes:
        return b"ciphertext"

    def decrypt(self, blob: bytes) -> dict:
        return {"credential": "x"}


ENGINE = FakeEngine()
STORE = FakeStore()


async def accepts(key: str, credential: str) -> VerifiedCredential:
    return VerifiedCredential(
        account_label="verified-account",
        scopes=("repo", "read:user"),
    )


async def rejects(key: str, credential: str) -> VerifiedCredential:
    raise CredentialRejected("GitHub rejected that token.")


async def unavailable(key: str, credential: str) -> VerifiedCredential:
    raise VerificationUnavailable("Could not reach GitHub.")


def _database_url() -> str | None:
    try:
        from api.settings import get_settings

        return get_settings().database_url

    except Exception:
        return None


async def _make_user(session: AsyncSession) -> User:
    user = User(
        email=f"connect-{uuid.uuid4().hex[:8]}@verify.test",
        password_hash="not-a-real-hash",
        full_name="Connect Test",
    )

    session.add(user)
    await session.flush()

    return user


async def _connections(session: AsyncSession, user_id) -> list:
    return list(
        await session.scalars(
            select(PluginConnection).where(
                PluginConnection.user_id == user_id
            )
        )
    )


def run_in_transaction(body) -> bool:
    url = _database_url()

    if not url:
        return False

    async def main() -> bool:
        engine = create_async_engine(url)

        try:
            conn = await engine.connect()

        except Exception:
            await engine.dispose()
            return False

        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        try:
            await body(session)

        finally:
            await session.close()
            await trans.rollback()
            await conn.close()
            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


# ---------------------------------------------------------------------


def test_a_rejected_credential_leaves_no_row():
    async def body(session: AsyncSession) -> None:
        user = await _make_user(session)

        raised = False

        try:
            await plugin_service.connect(
                session,
                ENGINE,
                STORE,
                user.id,
                "github",
                credential="hello-world-1234",
                verifier=rejects,
            )

        except CredentialRejected:
            raised = True

        assert raised

        # THE ASSERTION THAT MATTERS. A verified-then-stored-anyway
        # implementation passes every test that only checks the
        # exception.
        assert await _connections(session, user.id) == []

    if not run_in_transaction(body):
        _skipped("test_a_rejected_credential_leaves_no_row")


def test_an_unreachable_service_leaves_no_row():
    # "We could not check" must not become "connected". It is a
    # different message from a rejection, but the same restraint.
    async def body(session: AsyncSession) -> None:
        user = await _make_user(session)

        raised = False

        try:
            await plugin_service.connect(
                session,
                ENGINE,
                STORE,
                user.id,
                "github",
                credential="ghp_probably_fine",
                verifier=unavailable,
            )

        except VerificationUnavailable:
            raised = True

        assert raised
        assert await _connections(session, user.id) == []

    if not run_in_transaction(body):
        _skipped("test_an_unreachable_service_leaves_no_row")


def test_a_verified_credential_is_stored_and_connected():
    async def body(session: AsyncSession) -> None:
        user = await _make_user(session)

        result = await plugin_service.connect(
            session,
            ENGINE,
            STORE,
            user.id,
            "github",
            credential="ghp_a_real_looking_token",
            verifier=accepts,
        )

        assert result.status == "connected"

        rows = await _connections(session, user.id)

        assert len(rows) == 1
        assert rows[0].credentials_enc == b"ciphertext"

    if not run_in_transaction(body):
        _skipped("test_a_verified_credential_is_stored_and_connected")


def test_the_label_comes_from_the_service_not_the_browser():
    # It used to be stored straight from the request body, so a
    # connection could sit in the list labelled "finance@company.com"
    # while holding somebody's personal token. A label is a claim about
    # an account; only the account can make it.
    async def body(session: AsyncSession) -> None:
        user = await _make_user(session)

        result = await plugin_service.connect(
            session,
            ENGINE,
            STORE,
            user.id,
            "github",
            credential="ghp_a_real_looking_token",
            account_label="finance@company.com",
            scopes=["admin:everything"],
            verifier=accepts,
        )

        assert result.account_label == "verified-account"
        assert result.scopes == ["repo", "read:user"]

    if not run_in_transaction(body):
        _skipped("test_the_label_comes_from_the_service_not_the_browser")


def test_a_clients_label_survives_when_the_service_offers_none():
    # Fallback, not override. A service that reports no identity should
    # not wipe the note the user wrote to tell two accounts apart.
    async def body(session: AsyncSession) -> None:
        user = await _make_user(session)

        async def anonymous(key: str, credential: str) -> VerifiedCredential:
            return VerifiedCredential(account_label=None, scopes=())

        result = await plugin_service.connect(
            session,
            ENGINE,
            STORE,
            user.id,
            "github",
            credential="ghp_a_real_looking_token",
            account_label="my work account",
            verifier=anonymous,
        )

        assert result.account_label == "my work account"

    if not run_in_transaction(body):
        _skipped("test_a_clients_label_survives_when_the_service_offers_none")


def test_reconnecting_with_a_bad_token_keeps_the_good_one():
    # The dangerous case: a WORKING connection already exists, and the
    # user pastes something wrong. Replacing the credential before
    # checking it would break a service that was fine a moment ago -
    # turning a typo into an outage.
    async def body(session: AsyncSession) -> None:
        user = await _make_user(session)

        await plugin_service.connect(
            session,
            ENGINE,
            STORE,
            user.id,
            "github",
            credential="ghp_the_good_one",
            verifier=accepts,
        )

        try:
            await plugin_service.connect(
                session,
                ENGINE,
                STORE,
                user.id,
                "github",
                credential="typo",
                verifier=rejects,
            )

        except CredentialRejected:
            pass

        rows = await _connections(session, user.id)

        assert len(rows) == 1
        assert rows[0].status == "connected"
        assert rows[0].account_label == "verified-account"

    if not run_in_transaction(body):
        _skipped("test_reconnecting_with_a_bad_token_keeps_the_good_one")
