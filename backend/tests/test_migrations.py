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


def _upgrade_sqlite_to_revision(db_path, revision: str) -> None:
    env = {**os.environ, "CABINET_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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


def test_init_db_upgrades_stale_versioned_sqlite(tmp_path, monkeypatch):
    from app.db.base import dispose_engine, init_db

    _fresh_sqlite_db(tmp_path, monkeypatch, "stale_versioned.db")
    db_path = tmp_path / "stale_versioned.db"
    _upgrade_sqlite_to_revision(db_path, "b7d4e1a9c3f2")

    async def run() -> None:
        await init_db()

    try:
        asyncio.run(run())
        with sqlite3.connect(db_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(rooms)").fetchall()
            }
            assert {"logo_blob_path", "logo_source"} <= columns
            assert (
                connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                != "b7d4e1a9c3f2"
            )
    finally:
        asyncio.run(dispose_engine())


def test_init_db_rebuilds_sqlite_with_missing_revision(tmp_path, monkeypatch):
    from app.db.base import dispose_engine, init_db

    _fresh_sqlite_db(tmp_path, monkeypatch, "missing_revision.db")
    db_path = tmp_path / "missing_revision.db"
    _upgrade_sqlite_to_revision(db_path, "b7d4e1a9c3f2")

    room_id = "room-1"
    member_id = "member-1"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO rooms "
            "(id, customer_name, enrichment_prompt, status, cycles_used, cycle_limit, created_by, created_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                room_id,
                "Recovery Bank",
                None,
                "active",
                0,
                6,
                "dev@thetaray.com",
                "2026-07-15T00:00:00+00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO room_members "
            "(id, room_id, user_email, display_name, role, joined_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                member_id,
                room_id,
                "dev@thetaray.com",
                "Dev User",
                "owner",
                "2026-07-15T00:00:00+00:00",
            ),
        )
        connection.execute(
            "UPDATE alembic_version SET version_num = ?",
            ("8c4f8c3a91de",),
        )
        connection.commit()

    async def run() -> None:
        await init_db()

    try:
        asyncio.run(run())
        with sqlite3.connect(db_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(rooms)").fetchall()
            }
            assert {"logo_blob_path", "logo_source"} <= columns
            room = connection.execute(
                "SELECT customer_name, logo_source FROM rooms WHERE id = ?",
                (room_id,),
            ).fetchone()
            assert room == ("Recovery Bank", "none")
            assert (
                connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                != "8c4f8c3a91de"
            )
        assert list(tmp_path.glob("missing_revision.db.missing-revision-*.bak"))
    finally:
        asyncio.run(dispose_engine())
