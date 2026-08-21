"""
Phase 5.4 - re-encrypt every stored credential under the newest key.

    python scripts/rotate_credentials.py            # show what would change
    python scripts/rotate_credentials.py --apply    # do it

WHY A KEY EVER NEEDS ROTATING

Because the key eventually leaks - a laptop, a backup, a screen share,
an ex-employee - and the answer to a leaked key cannot be "ask every
user to reconnect every service". It has to be a job you can run while
the application keeps serving.

THE THREE-STATE DEPLOY

    1. BEFORE   CREDENTIAL_ENCRYPTION_KEYS=old
                every row written with `old`

    2. DURING   CREDENTIAL_ENCRYPTION_KEYS=new,old      <- deploy this
                new writes use `new`; old rows still decrypt.
                Now run this script.

    3. AFTER    CREDENTIAL_ENCRYPTION_KEYS=new          <- deploy this
                only once this script reports 0 remaining

At no point is any credential unreadable, and no user is asked to do
anything. Step 3 before the script finishes is the one mistake that
hurts: rows still on the old key become permanently undecryptable.

WHAT THIS DOES NOT DO

It never handles a plaintext credential. MultiFernet.rotate decrypts
and re-encrypts inside one call (see FernetCredentialStore.rotate), so
there is no moment where a token exists in a variable this file could
print, log, or leave in a traceback.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from api.credentials import CredentialError, build_credential_store  # noqa: E402
from api.db.models import PluginConnection  # noqa: E402
from api.settings import get_settings  # noqa: E402


# Rows per transaction.
#
# Not one big UPDATE: a rotation over a large table would hold every
# row locked for the whole run, and any user reconnecting a service in
# that window would block. Small batches keep each lock short.
BATCH_SIZE = 100


async def rotate(apply: bool) -> int:

    settings = get_settings()
    store = build_credential_store(settings)

    print(f"configured keys : {len(settings.encryption_keys)}")
    print(f"current version : {store.version}")

    if len(settings.encryption_keys) < 2 and apply:
        print(
            "\nOnly one key is configured, so there is nothing to rotate "
            "TO.\nSet CREDENTIAL_ENCRYPTION_KEYS=<new>,<old> first."
        )
        return 1

    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    rotated = 0
    failed = 0

    try:
        async with maker() as session:

            stale = await session.scalar(
                select(func.count())
                .select_from(PluginConnection)
                .where(PluginConnection.key_version < store.version)
            )

            print(f"rows to rotate  : {stale}")

            if not stale:
                print("\nNothing to do. Every row is on the current key.")
                return 0

            if not apply:
                print("\nDry run. Re-run with --apply to rotate.")
                return 0

        while True:

            async with maker() as session:

                rows = list(
                    await session.scalars(
                        select(PluginConnection)
                        .where(PluginConnection.key_version < store.version)
                        # FOR UPDATE SKIP LOCKED: another process - a
                        # user reconnecting a service right now - may
                        # hold a row. Skipping it and coming back is
                        # better than blocking the whole batch on one
                        # row, and the next pass picks it up.
                        .with_for_update(skip_locked=True)
                        .limit(BATCH_SIZE)
                    )
                )

                if not rows:
                    break

                for row in rows:

                    try:
                        row.credentials_enc = store.rotate(
                            bytes(row.credentials_enc)
                        )
                        row.key_version = store.version
                        rotated += 1

                    except CredentialError:
                        # The key that wrote this row is not configured
                        # at all. Left ALONE, deliberately: overwriting
                        # it would destroy a credential that the right
                        # key could still recover.
                        failed += 1

                        print(
                            f"  SKIP  {row.plugin_key} for user "
                            f"{row.user_id} - no configured key fits"
                        )

                        # Bump the version anyway or this row is picked
                        # up forever and the loop never ends. The data
                        # is untouched; only the marker moves.
                        row.key_version = store.version

                await session.commit()

                print(f"  ...{rotated} rotated")

    finally:
        await engine.dispose()

    print(f"\nrotated : {rotated}")
    print(f"skipped : {failed}")

    if failed:
        print(
            "\nSome rows could not be re-encrypted. Those users must "
            "reconnect the service.\nDo NOT drop the old key until you "
            "are sure it is not one of them."
        )

    return 0


def main() -> int:
    apply = "--apply" in sys.argv

    if not apply:
        print("DRY RUN - nothing will be written.\n")

    return asyncio.run(rotate(apply))


if __name__ == "__main__":
    raise SystemExit(main())
