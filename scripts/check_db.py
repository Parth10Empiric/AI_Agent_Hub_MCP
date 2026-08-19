"""
Phase 3 step 6 - verify the database is actually usable.

Run this BEFORE writing any SQLAlchemy models. Every failure it reports
is one you would otherwise meet later, disguised as an ORM bug:

    python scripts/check_db.py

It checks four things, in the order they can fail:

    1. DATABASE_URL is present, and uses the ASYNC driver
    2. the server accepts a connection to the `agenthub` database
    3. JSONB and TEXT[] work - the two PostgreSQL types the Phase 3.2
       schema depends on and SQLite does not have
    4. CREDENTIAL_ENCRYPTION_KEY is a valid Fernet key
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv


load_dotenv()

OK = "  OK  "
BAD = " FAIL "


def fail(message: str, fix: str) -> None:
    print(f"{BAD} {message}")
    print(f"       -> {fix}")
    sys.exit(1)


def check_url() -> str:
    url = os.getenv("DATABASE_URL", "")

    if not url:
        fail(
            "DATABASE_URL is not set",
            "copy .env.example to .env and fill it in",
        )

    if "YOUR_PASSWORD" in url or "CHANGEME" in url:
        fail(
            "DATABASE_URL still contains the placeholder password",
            "edit .env and replace it with your postgres password",
        )

    # The whole point of Phase 3 step 4 was to stop blocking the event
    # loop. A synchronous driver here would undo it silently - every
    # query would freeze all other users, and nothing would look wrong.
    if "+asyncpg" not in url:
        fail(
            f"DATABASE_URL does not use the async driver: {url.split('@')[0]}...",
            "use postgresql+asyncpg://  (not plain postgresql://)",
        )

    print(f"{OK} DATABASE_URL set, async driver")

    return url


async def check_connection(url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url, echo=False)

    try:
        async with engine.connect() as conn:

            version = await conn.scalar(text("SELECT version()"))
            print(f"{OK} connected: {version.split(',')[0]}")

            database = await conn.scalar(text("SELECT current_database()"))
            print(f"{OK} database : {database}")

            # The two types SQLite cannot do. If these pass, the whole
            # Phase 3.2 schema will build.
            await conn.execute(
                text("SELECT '{\"a\": 1}'::jsonb -> 'a'")
            )
            await conn.execute(
                text("SELECT ARRAY['read', 'write']::text[]")
            )
            print(f"{OK} JSONB and TEXT[] supported")

    except Exception as exc:
        name = type(exc).__name__

        hint = "is the postgresql-x64-18 service running?"

        if "password" in str(exc).lower():
            hint = "wrong password in DATABASE_URL"
        elif "does not exist" in str(exc).lower():
            hint = "create the database first (see pgAdmin steps)"

        fail(f"{name}: {exc}", hint)

    finally:
        await engine.dispose()


def check_secrets() -> None:
    from cryptography.fernet import Fernet

    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")

    if not key:
        fail(
            "CREDENTIAL_ENCRYPTION_KEY is not set",
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"',
        )

    try:
        secret = b"a plugin oauth token"
        assert Fernet(key.encode()).decrypt(
            Fernet(key.encode()).encrypt(secret)
        ) == secret
    except Exception as exc:
        fail(f"CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key: {exc}",
             "regenerate it with Fernet.generate_key()")

    print(f"{OK} CREDENTIAL_ENCRYPTION_KEY encrypts and decrypts")

    jwt_secret = os.getenv("JWT_SECRET_KEY", "")

    if len(jwt_secret) < 32:
        fail(
            "JWT_SECRET_KEY is missing or too short",
            'python -c "import secrets; print(secrets.token_urlsafe(48))"',
        )

    print(f"{OK} JWT_SECRET_KEY set ({len(jwt_secret)} chars)")


async def main() -> None:
    print("\nPhase 3 step 6 - database check")
    print("-" * 52)

    url = check_url()
    await check_connection(url)
    check_secrets()

    print("-" * 52)
    print("All checks passed. Ready for Phase3.md step 1 (api/ skeleton).\n")


if __name__ == "__main__":
    asyncio.run(main())
