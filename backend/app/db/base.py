"""Async SQLAlchemy engine/session wiring.

Production targets Azure Database for PostgreSQL (Flexible Server) via
``postgresql+asyncpg://``; tests and local dev run the identical models on
``sqlite+aiosqlite``. The engine is created lazily so tests can point
CABINET_DATABASE_URL at a temp database before first use.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings, reset_settings_cache


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"
_DEV_SQLITE_COPY_SPECS: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "agent_global_config",
        ("agent_key", "display_name", "system_prompt", "updated_at"),
        ("agent_key", "display_name", "system_prompt", "updated_at"),
    ),
    (
        "rooms",
        (
            "id",
            "customer_name",
            "enrichment_prompt",
            "status",
            "cycles_used",
            "cycle_limit",
            "created_by",
            "created_at",
            "deleted_at",
        ),
        (
            "id",
            "customer_name",
            "enrichment_prompt",
            "status",
            "cycles_used",
            "cycle_limit",
            "created_by",
            "created_at",
            "NULL",
        ),
    ),
    (
        "room_agents",
        (
            "id",
            "room_id",
            "agent_key",
            "display_name",
            "instructions",
            "created_at",
        ),
        ("id", "room_id", "agent_key", "display_name", "''", "created_at"),
    ),
    (
        "room_members",
        ("id", "room_id", "user_email", "display_name", "role", "joined_at"),
        ("id", "room_id", "user_email", "display_name", "role", "joined_at"),
    ),
    (
        "room_invites",
        ("token", "room_id", "created_by", "expires_at", "created_at"),
        ("token", "room_id", "created_by", "expires_at", "created_at"),
    ),
    (
        "gdrive_connections",
        (
            "id",
            "room_id",
            "google_folder_id",
            "google_folder_name",
            "access_token_enc",
            "refresh_token_enc",
            "token_expiry",
            "scopes",
            "status",
            "created_at",
            "updated_at",
        ),
        (
            "id",
            "room_id",
            "google_folder_id",
            "google_folder_name",
            "access_token_enc",
            "refresh_token_enc",
            "token_expiry",
            "scopes",
            "status",
            "created_at",
            "updated_at",
        ),
    ),
    (
        "agent_skills",
        (
            "id",
            "room_id",
            "agent_key",
            "skill_name",
            "skill_type",
            "blob_path",
            "content_text",
            "created_at",
        ),
        (
            "id",
            "room_id",
            "agent_key",
            "skill_name",
            "skill_type",
            "blob_path",
            "content_text",
            "created_at",
        ),
    ),
    (
        "room_skill_overrides",
        ("room_id", "skill_id", "created_at"),
        ("room_id", "skill_id", "created_at"),
    ),
    (
        "messages",
        (
            "id",
            "seq",
            "room_id",
            "sender_type",
            "sender_name",
            "agent_key",
            "mention_target",
            "edit_of_id",
            "cycle_number",
            "content",
            "input_tokens",
            "output_tokens",
            "created_at",
            "superseded_at",
        ),
        (
            "id",
            "seq",
            "room_id",
            "sender_type",
            "sender_name",
            "agent_key",
            "mention_target",
            "NULL",
            "cycle_number",
            "content",
            "input_tokens",
            "output_tokens",
            "created_at",
            "NULL",
        ),
    ),
    (
        "audit_log",
        ("id", "room_id", "actor", "action", "detail", "created_at"),
        ("id", "room_id", "actor", "action", "detail", "created_at"),
    ),
)


def _sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None
    if not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _sqlite_user_tables(conn: sqlite3.Connection, schema: str = "main") -> set[str]:
    rows = conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in rows}


def _sqlite_has_recorded_revision(conn: sqlite3.Connection) -> bool:
    if "alembic_version" not in _sqlite_user_tables(conn):
        return False
    row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    return bool(row and isinstance(row[0], str) and row[0].strip())


def _needs_legacy_sqlite_rebuild(database_url: str) -> bool:
    db_path = _sqlite_database_path(database_url)
    if db_path is None or not db_path.exists():
        return False
    with sqlite3.connect(db_path) as conn:
        tables = _sqlite_user_tables(conn) - {"alembic_version"}
        return bool(tables) and not _sqlite_has_recorded_revision(conn)


def _sqlite_url_with_path(database_url: str, path: Path) -> str:
    return str(make_url(database_url).set(database=str(path)))


def _run_alembic_upgrade(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    original_database_url = os.environ.get("CABINET_DATABASE_URL")
    os.environ["CABINET_DATABASE_URL"] = database_url
    reset_settings_cache()
    try:
        config = Config(str(ALEMBIC_INI_PATH))
        command.upgrade(config, "head")
    finally:
        if original_database_url is None:
            os.environ.pop("CABINET_DATABASE_URL", None)
        else:
            os.environ["CABINET_DATABASE_URL"] = original_database_url
        reset_settings_cache()


def _legacy_backup_path(db_path: Path) -> Path:
    candidate = db_path.with_name(f"{db_path.name}.legacy-pre-alembic.bak")
    index = 1
    while candidate.exists():
        candidate = db_path.with_name(
            f"{db_path.name}.legacy-pre-alembic.{index}.bak"
        )
        index += 1
    return candidate


def _copy_legacy_sqlite_data(legacy_db_path: Path, rebuilt_db_path: Path) -> None:
    legacy_path_sql = str(legacy_db_path).replace("'", "''")
    conn = sqlite3.connect(rebuilt_db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(f"ATTACH DATABASE '{legacy_path_sql}' AS legacy")
        legacy_tables = _sqlite_user_tables(conn, "legacy")
        with conn:
            for table, _, _ in reversed(_DEV_SQLITE_COPY_SPECS):
                conn.execute(f"DELETE FROM {table}")
            for table, dest_cols, src_exprs in _DEV_SQLITE_COPY_SPECS:
                if table not in legacy_tables:
                    continue
                dest_sql = ", ".join(dest_cols)
                src_sql = ", ".join(src_exprs)
                conn.execute(
                    f"INSERT INTO {table} ({dest_sql}) "
                    f"SELECT {src_sql} FROM legacy.{table}"
                )
        conn.execute("DETACH DATABASE legacy")
        conn.execute("PRAGMA foreign_keys=ON")
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"legacy sqlite rebuild failed foreign_key_check: {problems}")
    finally:
        conn.close()


def _rebuild_legacy_sqlite_db(database_url: str) -> None:
    db_path = _sqlite_database_path(database_url)
    if db_path is None:
        raise RuntimeError("legacy sqlite rebuild requires a file-backed SQLite database")

    rebuilt_db_path = db_path.with_name(f"{db_path.name}.rebuilt")
    backup_path = _legacy_backup_path(db_path)
    if rebuilt_db_path.exists():
        rebuilt_db_path.unlink()
    rebuilt_db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _run_alembic_upgrade(_sqlite_url_with_path(database_url, rebuilt_db_path))
        _copy_legacy_sqlite_data(db_path, rebuilt_db_path)
        os.replace(db_path, backup_path)
        try:
            os.replace(rebuilt_db_path, db_path)
        except Exception:
            os.replace(backup_path, db_path)
            raise
    finally:
        if rebuilt_db_path.exists():
            rebuilt_db_path.unlink()


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
    if get_settings().env != "dev":
        # staging/production: schema is managed by `alembic upgrade head` as
        # a release step, never by app startup — N replicas racing
        # `create_all`/DDL is exactly the H13 bug this design fixes.
        return

    database_url = get_settings().database_url
    sqlite_path = _sqlite_database_path(database_url)
    if database_url.startswith("sqlite") and sqlite_path is None:
        from . import models  # noqa: F401 — register mappings

        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    if _needs_legacy_sqlite_rebuild(database_url):
        await asyncio.to_thread(_rebuild_legacy_sqlite_db, database_url)
    await asyncio.to_thread(_run_alembic_upgrade, database_url)


async def dispose_engine() -> None:
    """Test helper: tear down the cached engine between test sessions."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
