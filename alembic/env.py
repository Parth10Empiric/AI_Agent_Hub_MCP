"""
Alembic environment.

Two changes from the generated template, both necessary:

  1. target_metadata points at OUR Base.metadata, and every model is
     imported so it is actually registered on it.

  2. The database URL comes from APISettings, not from alembic.ini.
     alembic.ini is committed to git; the URL contains the database
     password. They must never meet.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Importing the models package registers all seven tables on
# Base.metadata. Autogenerate compares that metadata against the live
# database, so a model that is not imported here is INVISIBLE: Alembic
# will either never create its table or generate a migration that drops
# it.
import api.db.models  # noqa: F401
from api.db.base import Base
from api.settings import get_settings


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# What "the schema should look like". Autogenerate diffs this against
# what the database actually has.
target_metadata = Base.metadata


# Injected at runtime rather than read from alembic.ini, so the
# password lives only in .env.
config.set_main_option(
    "sqlalchemy.url",
    get_settings().database_url,
)


# Options that change what autogenerate can SEE.
#
# Both are off by default, and the defaults are a trap: without them
# Alembic detects added and dropped columns but silently ignores a
# column whose TYPE changed. You would edit String(120) -> String(255),
# autogenerate an empty migration, and conclude the change was applied.
CONTEXT_OPTIONS = {
    "compare_type": True,
    "compare_server_default": True,
}


def run_migrations_offline() -> None:
    """
    Generate SQL without connecting to anything.

        alembic upgrade head --sql > migration.sql

    Useful when a DBA has to review or apply the change by hand, which
    is how migrations reach many production databases.
    """

    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONTEXT_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **CONTEXT_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations against a live database.

    NullPool because this process runs one migration and exits.
    Connection pooling exists to amortise connection cost across many
    requests; there is only one here, and a pooled connection left open
    at exit can hold a lock the application then waits on.
    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
