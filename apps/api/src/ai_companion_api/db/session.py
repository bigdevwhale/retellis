"""Async engine + session factory.

The engine is created lazily from ``settings.database_url``. Only ``PostgresStore``
imports this — the in-memory default path never touches it, so ``docker compose up``
works without a reachable Postgres (the web + API still serve; memory is process-local).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import Settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings) -> AsyncEngine:
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker  # noqa: PLW0603
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(settings), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def session(settings: Settings) -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker(settings)
    async with sm() as s:
        yield s


async def dispose() -> None:
    """Dispose the engine — call on shutdown."""
    global _engine, _sessionmaker  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_for_tests() -> None:
    """Test helper: drop cached engine so a new DATABASE_URL takes effect."""
    global _engine, _sessionmaker  # noqa: PLW0603
    _engine = None
    _sessionmaker = None


__all__ = ["dispose", "get_engine", "get_sessionmaker", "reset_for_tests", "session"]
