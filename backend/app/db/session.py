"""Async SQLAlchemy engine, session factory, and transaction helpers.

The ``transaction()`` context manager is the ONLY sanctioned way to do multi-statement
writes (e.g. dedup's delete-then-reinsert) — it guarantees atomic commit/rollback. Use
``serializable=True`` for dedup writes that must detect write-write conflicts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Separate engine pinned to SERIALIZABLE isolation for dedup's concurrent-insert protection.
_serializable_engine = create_async_engine(
    _settings.database_url, pool_pre_ping=True, isolation_level="SERIALIZABLE"
)
SerializableSessionLocal = async_sessionmaker(
    _serializable_engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session (no implicit transaction)."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def transaction(*, serializable: bool = False) -> AsyncIterator[AsyncSession]:
    """Open a session inside a single transaction.

    Commits on clean exit, rolls back on any exception. Use ``serializable=True`` for
    writes that must not race (dedup). On a serialization failure Postgres raises and the
    caller should retry.
    """
    factory = SerializableSessionLocal if serializable else AsyncSessionLocal
    async with factory() as session:
        async with session.begin():
            yield session
