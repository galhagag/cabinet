"""Alembic migration roundtrip (Design 05 / H13)."""
import asyncio
import os
import sqlite3
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_alembic_upgrade_and_downgrade_roundtrip(tmp_path):
    db_path = tmp_path / "alembic_test.db"
    env = {**os.environ, "CABINET_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert downgrade.returncode == 0, downgrade.stderr


def _fresh_sqlite_db(tmp_path, monkeypatch, name: str) -> None:
    from app.config import reset_settings_cache
    from app.db.base import dispose_engine

    monkeypatch.setenv(
        "CABINET_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / name}"
    )
    monkeypatch.setenv("CABINET_SKIP_LOCAL_DOTENV", "1")
    reset_settings_cache()
    asyncio.run(dispose_engine())


def test_check_constraint_rejects_invalid_room_status(tmp_path, monkeypatch):
    """The Design 05 test plan promised 'writing an invalid status raises at
    the DB layer' but no test ever exercised it. ``ck_rooms_status`` is
    inline in the ``CREATE TABLE`` DDL that ``create_all``/Alembic emit, so
    SQLite enforces it out of the box — unlike FK ``ondelete`` policies,
    which need ``PRAGMA foreign_keys=ON`` (see the RESTRICT test below)."""
    from app.db.base import dispose_engine, get_sessionmaker, init_db
    from app.db.models import Room

    _fresh_sqlite_db(tmp_path, monkeypatch, "check_test.db")

    async def run() -> None:
        await init_db()
        async with get_sessionmaker()() as session:
            session.add(
                Room(customer_name="BogusStatusCo", status="not_a_real_status")
            )
            try:
                await session.commit()
                raise AssertionError(
                    "expected IntegrityError from ck_rooms_status"
                )
            except IntegrityError:
                await session.rollback()

    try:
        asyncio.run(run())
    finally:
        asyncio.run(dispose_engine())


def test_sqlite_enforces_message_room_id_restrict(tmp_path, monkeypatch):
    """``Message.room_id`` is ``ondelete=RESTRICT`` specifically so the audit
    transcript can't be silently destroyed (Design 05 / M17). SQLite ignores
    every FK ``ondelete`` policy — CASCADE, RESTRICT, SET NULL alike — unless
    ``PRAGMA foreign_keys=ON`` is set per connection (off by default). This
    confirms ``db/base.py``'s ``get_engine()`` turns that on for sqlite, so
    the RESTRICT protection actually holds in dev/tests and isn't a
    Postgres-only no-op silently uncovered by the suite."""
    from app.db.base import dispose_engine, get_sessionmaker, init_db
    from app.db.models import Message, Room

    _fresh_sqlite_db(tmp_path, monkeypatch, "fk_restrict_test.db")

    async def run() -> None:
        await init_db()
        async with get_sessionmaker()() as session:
            room = Room(customer_name="RestrictCo")
            session.add(room)
            await session.flush()
            session.add(
                Message(
                    room_id=room.id,
                    sender_type="human",
                    sender_name="x",
                    content="hi",
                )
            )
            await session.commit()
            room_id = room.id

        async with get_sessionmaker()() as session:
            try:
                await session.execute(
                    text("DELETE FROM rooms WHERE id = :id"), {"id": room_id}
                )
                await session.commit()
                raise AssertionError(
                    "expected an FK RESTRICT violation deleting a room "
                    "that still has messages"
                )
            except IntegrityError:
                await session.rollback()

    try:
        asyncio.run(run())
    finally:
        asyncio.run(dispose_engine())


def test_alembic_handles_percent_sign_in_database_url(tmp_path):
    """A real Postgres URL commonly carries a URL-encoded password (e.g.
    ``%40`` for ``@``). ``env.py`` round-trips the URL through
    ``alembic.config.Config.set_main_option``/``get_section``, which are
    backed by ``configparser`` — its default interpolation treats a bare
    ``%`` as the start of a ``%(name)s`` token and raises before a single
    migration runs unless the value is escaped first. Reproduce that shape
    with a literal ``%`` in the sqlite URL's path (dialect doesn't matter —
    the crash happens in Config, before a connection is ever opened)."""
    percent_dir = tmp_path / "pw%40db"
    percent_dir.mkdir()
    db_path = percent_dir / "alembic_test.db"
    env = {**os.environ, "CABINET_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr
    assert db_path.exists()

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert downgrade.returncode == 0, downgrade.stderr


def test_init_db_rebuilds_legacy_unversioned_sqlite(tmp_path, monkeypatch):
    """Legacy dev sqlite files created via create_all() were never Alembic-
    managed, so later additive columns could strand real local data behind a
    missing-column failure. init_db() should rebuild that shape into the
    current schema without losing the rows."""
    from app.db.base import dispose_engine, init_db

    db_path = tmp_path / "legacy_create_all.db"
    _fresh_sqlite_db(tmp_path, monkeypatch, db_path.name)

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE rooms (
                id VARCHAR(36) PRIMARY KEY,
                customer_name VARCHAR(256) NOT NULL UNIQUE,
                enrichment_prompt TEXT,
                status VARCHAR(32) NOT NULL,
                cycles_used INTEGER NOT NULL,
                cycle_limit INTEGER NOT NULL,
                created_by VARCHAR(256) NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE room_agents (
                id VARCHAR(36) PRIMARY KEY,
                room_id VARCHAR(36) NOT NULL,
                agent_key VARCHAR(32) NOT NULL,
                display_name VARCHAR(128) NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
            );
            CREATE TABLE room_members (
                id VARCHAR(36) PRIMARY KEY,
                room_id VARCHAR(36) NOT NULL,
                user_email VARCHAR(256) NOT NULL,
                display_name VARCHAR(256) NOT NULL,
                role VARCHAR(16) NOT NULL,
                joined_at DATETIME NOT NULL,
                FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
            );
            CREATE TABLE messages (
                id VARCHAR(36) PRIMARY KEY,
                seq BIGINT NOT NULL,
                room_id VARCHAR(36) NOT NULL,
                sender_type VARCHAR(16) NOT NULL,
                sender_name VARCHAR(256) NOT NULL,
                agent_key VARCHAR(32),
                mention_target VARCHAR(32),
                cycle_number INTEGER,
                content TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE RESTRICT
            );
            """
        )
        conn.execute(
            "INSERT INTO rooms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "room-1",
                "LegacyCo",
                "legacy prompt",
                "active",
                2,
                6,
                "legacy@example.com",
                "2026-07-12T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO room_agents VALUES (?, ?, ?, ?, ?)",
            (
                "agent-1",
                "room-1",
                "data_expert",
                "Data Expert",
                "2026-07-12T00:00:01Z",
            ),
        )
        conn.execute(
            "INSERT INTO room_members VALUES (?, ?, ?, ?, ?, ?)",
            (
                "member-1",
                "room-1",
                "legacy@example.com",
                "Legacy User",
                "owner",
                "2026-07-12T00:00:02Z",
            ),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "msg-1",
                1,
                "room-1",
                "human",
                "Legacy User",
                None,
                None,
                1,
                "hello from legacy sqlite",
                None,
                None,
                "2026-07-12T00:00:03Z",
            ),
        )

    async def run() -> None:
        await init_db()

    try:
        asyncio.run(run())
        with sqlite3.connect(db_path) as conn:
            room_columns = {row[1] for row in conn.execute("PRAGMA table_info(rooms)")}
            room_agent_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(room_agents)")
            }
            message_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(messages)")
            }
            assert "deleted_at" in room_columns
            assert "instructions" in room_agent_columns
            assert {"edit_of_id", "superseded_at"}.issubset(message_columns)
            assert conn.execute(
                "SELECT customer_name FROM rooms WHERE id = 'room-1'"
            ).fetchone() == ("LegacyCo",)
            assert conn.execute(
                "SELECT instructions FROM room_agents WHERE id = 'agent-1'"
            ).fetchone() == ("",)
            assert conn.execute(
                "SELECT edit_of_id, superseded_at FROM messages WHERE id = 'msg-1'"
            ).fetchone() == (None, None)
            version = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            assert version and version[0]
        backups = list(tmp_path.glob("legacy_create_all.db.legacy-pre-alembic*.bak"))
        assert backups
    finally:
        asyncio.run(dispose_engine())
