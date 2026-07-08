"""Async SQLAlchemy engine/session wiring.

Production targets Azure Database for PostgreSQL (Flexible Server) via
``postgresql+asyncpg://``; tests and local dev run the identical models on
``sqlite+aiosqlite``. The engine is created lazily so tests can point
CABINET_DATABASE_URL at a temp database before first use.
"""
from __future__ import annotations

from typing import AsyncIterator

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

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Test helper: tear down the cached engine between test sessions."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
