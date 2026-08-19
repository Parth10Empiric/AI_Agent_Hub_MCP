from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.settings import APISettings


def build_engine(settings: APISettings) -> AsyncEngine:
    """
    Create the connection pool. ONE per application.

    Opening a PostgreSQL connection costs a TCP handshake plus
    authentication - tens of milliseconds. The engine keeps a pool of
    open connections and lends them out. An engine per request would
    throw that away and exhaust the server's connection limit at
    roughly a hundred users.

    pool_pre_ping issues a cheap SELECT 1 before handing over a
    connection. Without it, a connection the database closed while idle
    (a restart, an idle timeout, a firewall) is discovered only when a
    real query fails - as a random error on an unrelated request.
    """

    return create_async_engine(
        settings.database_url,

        # Set to True to see every SQL statement. Extremely useful when
        # a query does something you did not expect, and far too noisy
        # to leave on.
        echo=False,

        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )


def build_sessionmaker(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """
    A factory that produces sessions. ONE per application.

    expire_on_commit=False is the important argument.

    By default SQLAlchemy expires every loaded object after a commit,
    so touching any attribute afterwards triggers a refresh query. In
    ASYNC code that refresh happens during plain attribute access,
    which cannot perform IO - so it raises MissingGreenlet instead.

    The symptom is baffling: your endpoint commits successfully and
    then explodes while serialising the response. Turning expiry off
    means the objects you already loaded stay usable after commit.
    """

    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency: one session per request.

    Read as:

        open a session
            hand it to the endpoint          <- yield
            the endpoint succeeded -> commit
            the endpoint raised    -> rollback, re-raise
        close the session

    The sessionmaker is read from app.state rather than a module-level
    global, for the same reason create_app() is a factory: a test must
    be able to point a second app at a test database. A global would be
    shared by both.

    Committing here rather than in every endpoint is deliberate. The
    alternative - each service calling commit() itself - fails silently
    when someone forgets: the session closes, the changes are discarded
    and the request still returns 200.
    """

    factory: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async with factory() as session:

        try:
            yield session
            await session.commit()

        except Exception:
            await session.rollback()
            raise
