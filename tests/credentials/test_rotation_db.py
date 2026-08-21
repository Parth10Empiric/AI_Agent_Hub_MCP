from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from api.credentials import CredentialError, FernetCredentialStore  # noqa: E402
from api.db.models import PluginConnection, User  # noqa: E402

"""
A rotation, end to end, against real PostgreSQL.

The unit tests prove the CRYPTO works. This proves the three-state
deploy works on rows: written under an old key, readable during the
overlap, re-encrypted in place, and finally readable by the new key
alone - which is the moment it becomes safe to delete the old one.
"""


KEY_OLD = Fernet.generate_key().decode()
KEY_NEW = Fernet.generate_key().decode()

OLD_STORE = FernetCredentialStore(KEY_OLD)
BOTH_STORE = FernetCredentialStore([KEY_NEW, KEY_OLD])
NEW_STORE = FernetCredentialStore(KEY_NEW)


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
            async with maker() as db:
                for user_id in created:
                    await db.execute(delete(User).where(User.id == user_id))
                await db.commit()

            await engine.dispose()

        return True

    return asyncio.run(main())


def _skipped(name: str) -> None:
    print(f"  SKIP  {name} (database unreachable)")


async def _connection_on_old_key(maker, created) -> uuid.UUID:
    """A user with one credential written under the retired key."""

    async with maker() as db:

        user = User(
            email=f"rotate-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="not-a-real-hash",
        )
        db.add(user)
        await db.flush()

        created.append(user.id)

        db.add(
            PluginConnection(
                user_id=user.id,
                plugin_key="github",
                credentials_enc=OLD_STORE.encrypt(
                    {"access_token": "ghp_original"}
                ),
                key_version=OLD_STORE.version,
                status="connected",
                scopes=[],
            )
        )

        await db.commit()

        return user.id


def test_a_row_written_under_the_old_key_survives_the_overlap():

    async def body(maker, created):

        user_id = await _connection_on_old_key(maker, created)

        async with maker() as db:
            row = await db.scalar(
                select(PluginConnection).where(
                    PluginConnection.user_id == user_id
                )
            )

            # DURING the rotation both keys are configured, so nothing
            # the application does breaks while the job runs.
            assert BOTH_STORE.decrypt(bytes(row.credentials_enc)) == {
                "access_token": "ghp_original"
            }

    if not run_committed(body):
        _skipped("test_a_row_written_under_the_old_key_survives_the_overlap")


def test_rotating_makes_the_row_readable_by_the_new_key_alone():

    async def body(maker, created):

        user_id = await _connection_on_old_key(maker, created)

        # --- the job -------------------------------------------------
        async with maker() as db:

            rows = list(
                await db.scalars(
                    select(PluginConnection)
                    .where(
                        PluginConnection.user_id == user_id,
                        PluginConnection.key_version < BOTH_STORE.version,
                    )
                    .with_for_update(skip_locked=True)
                )
            )

            assert len(rows) == 1

            for row in rows:
                row.credentials_enc = BOTH_STORE.rotate(
                    bytes(row.credentials_enc)
                )
                row.key_version = BOTH_STORE.version

            await db.commit()

        # --- after ---------------------------------------------------
        async with maker() as db:
            row = await db.scalar(
                select(PluginConnection).where(
                    PluginConnection.user_id == user_id
                )
            )

            assert row.key_version == BOTH_STORE.version

            # The old key can now be deleted: the new one alone reads
            # it, and the token itself never changed.
            assert NEW_STORE.decrypt(bytes(row.credentials_enc)) == {
                "access_token": "ghp_original"
            }

            # ...and the ciphertext still hides it.
            assert b"ghp_original" not in bytes(row.credentials_enc)

    if not run_committed(body):
        _skipped("test_rotating_makes_the_row_readable_by_the_new_key_alone")


def test_a_rotated_row_is_not_selected_again():
    # Without the key_version bump the job would pick the same rows
    # forever and never terminate.

    async def body(maker, created):

        user_id = await _connection_on_old_key(maker, created)

        async with maker() as db:
            row = await db.scalar(
                select(PluginConnection).where(
                    PluginConnection.user_id == user_id
                )
            )
            row.credentials_enc = BOTH_STORE.rotate(bytes(row.credentials_enc))
            row.key_version = BOTH_STORE.version
            await db.commit()

        async with maker() as db:
            remaining = list(
                await db.scalars(
                    select(PluginConnection).where(
                        PluginConnection.user_id == user_id,
                        PluginConnection.key_version < BOTH_STORE.version,
                    )
                )
            )

            assert remaining == []

    if not run_committed(body):
        _skipped("test_a_rotated_row_is_not_selected_again")


def test_dropping_the_old_key_before_the_job_finishes_loses_the_row():
    """
    The failure the three-state deploy exists to prevent, asserted so
    nobody "simplifies" it into two steps.
    """

    async def body(maker, created):

        user_id = await _connection_on_old_key(maker, created)

        async with maker() as db:
            row = await db.scalar(
                select(PluginConnection).where(
                    PluginConnection.user_id == user_id
                )
            )

            try:
                NEW_STORE.decrypt(bytes(row.credentials_enc))
                raise AssertionError(
                    "an un-rotated row survived losing its key"
                )

            except CredentialError:
                pass

    if not run_committed(body):
        _skipped("test_dropping_the_old_key_before_the_job_finishes_loses_the_row")
