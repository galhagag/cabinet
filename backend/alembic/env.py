import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.db import models  # noqa: F401 — register mappings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `Config.set_main_option`/`get_section` round-trip the value through
# `configparser`, which treats `%` as the start of a pyformat interpolation
# token (`%(name)s`). A real Postgres URL commonly carries a URL-encoded
# password (e.g. `%40` for `@`), so a literal, unescaped `%` here raises
# `configparser`'s `InterpolationSyntaxError`/`ValueError` before a single
# migration runs. Escaping to `%%` makes ConfigParser's own interpolation
# step decode it back to the original `%` on every read (get_main_option,
# get_section) below.
config.set_main_option(
    "sqlalchemy.url", get_settings().database_url.replace("%", "%%")
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
