"""Async SQLAlchemy engine/session wiring.

Production targets Azure Database for PostgreSQL (Flexible Server) via
``postgresql+asyncpg://``; tests and local dev run the identical models on
``sqlite+aiosqlite``. The engine is created lazily so tests can point
CABINET_DATABASE_URL at a temp database before first use.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
LOGGER = logging.getLogger(__name__)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    backend_root = _backend_root()
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("prepend_sys_path", str(backend_root))
    return config


def _sqlite_db_path(database_url: str) -> Path | None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database or ""
    if database in {"", ":memory:"}:
        return None
    return Path(database).expanduser()


def _quote_sqlite_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sqlite_user_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _sqlite_table_columns(
    connection: sqlite3.Connection, *, schema: str, table_name: str
) -> set[str]:
    pragma = f"PRAGMA {schema}.table_info({_quote_sqlite_ident(table_name)})"
    rows = connection.execute(pragma).fetchall()
    return {row[1] for row in rows}


def _sqlite_rebuild_reason(db_path: Path, known_revisions: set[str]) -> str | None:
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as connection:
        tables = _sqlite_user_tables(connection)
        if not tables:
            return None
        if "alembic_version" not in tables:
            return "legacy-pre-alembic"
        version_row = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
        version = version_row[0] if version_row else ""
        if not version:
            return "legacy-pre-alembic"
        if version not in known_revisions:
            return "missing-revision"
    return None


def _copy_sqlite_table_data(
    connection: sqlite3.Connection, *, source_schema: str, table_name: str
) -> None:
    source_columns = _sqlite_table_columns(
        connection, schema=source_schema, table_name=table_name
    )
    target_columns = _sqlite_table_columns(
        connection, schema="main", table_name=table_name
    )
    common_columns = [
        column.name
        for column in Base.metadata.tables[table_name].columns
        if column.name in source_columns and column.name in target_columns
    ]
    if not common_columns:
        return
    quoted_columns = ", ".join(_quote_sqlite_ident(column) for column in common_columns)
    quoted_table = _quote_sqlite_ident(table_name)
    connection.execute(
        f"INSERT INTO {quoted_table} ({quoted_columns}) "
        f"SELECT {quoted_columns} FROM {source_schema}.{quoted_table}"
    )


def _rebuild_sqlite_database(
    *, db_path: Path, config: Config, reason: str
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = db_path.with_name(f"{db_path.name}.{reason}-{stamp}.bak")
    shutil.move(db_path, backup_path)
    try:
        command.upgrade(config, "head")
        with sqlite3.connect(db_path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("ATTACH DATABASE ? AS legacy", (str(backup_path),))
            try:
                for table in Base.metadata.sorted_tables:
                    if table.name == "alembic_version":
                        continue
                    if table.name not in _sqlite_user_tables(connection):
                        continue
                    legacy_columns = _sqlite_table_columns(
                        connection, schema="legacy", table_name=table.name
                    )
                    if not legacy_columns:
                        continue
                    _copy_sqlite_table_data(
                        connection, source_schema="legacy", table_name=table.name
                    )
                connection.commit()
            finally:
                connection.execute("DETACH DATABASE legacy")
    except Exception:
        if db_path.exists():
            db_path.unlink()
        shutil.move(backup_path, db_path)
        raise
    return backup_path


def _upgrade_dev_database(database_url: str) -> None:
    config = _alembic_config()
    sqlite_path = _sqlite_db_path(database_url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        known_revisions = {
            revision.revision
            for revision in ScriptDirectory.from_config(config).walk_revisions()
        }
        rebuild_reason = _sqlite_rebuild_reason(sqlite_path, known_revisions)
        if rebuild_reason is not None:
            backup_path = _rebuild_sqlite_database(
                db_path=sqlite_path,
                config=config,
                reason=rebuild_reason,
            )
            LOGGER.warning(
                "Rebuilt local sqlite database %s (%s). Backup preserved at %s",
                sqlite_path,
                rebuild_reason,
                backup_path,
            )
            return
    command.upgrade(config, "head")


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            # Concurrent writers (loop-budget claims) must wait for the file
            # lock instead of failing with "database is locked".
            kwargs["connect_args"] = {"timeout": 30}
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            # SQLite ignores FK constraints (CASCADE/RESTRICT/SET NULL) per
            # connection unless PRAGMA foreign_keys=ON is set — off by
            # default. Without this, the ondelete= policies in models.py
            # (e.g. Message.room_id RESTRICT protecting the audit
            # transcript) are silently unenforced in dev/tests and only
            # actually take effect against Postgres in production.
            @event.listens_for(_engine.sync_engine, "connect")
            def _enable_sqlite_fk(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one AsyncSession per request."""
    async with get_sessionmaker()() as session:
        yield session


async def init_db() -> None:
    from . import models  # noqa: F401 — register mappings
    from ..config import get_settings

    settings = get_settings()
    if settings.env != "dev":
        # staging/production: schema is managed by `alembic upgrade head` as
        # a release step, never by app startup — N replicas racing
        # `create_all`/DDL is exactly the H13 bug this design fixes.
        return
    if _sqlite_db_path(settings.database_url) is None and settings.database_url.startswith(
        "sqlite"
    ):
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    await asyncio.to_thread(_upgrade_dev_database, settings.database_url)


async def dispose_engine() -> None:
    """Test helper: tear down the cached engine between test sessions."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
