"""Database engine and sessions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


def make_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        # One shared connection so every session sees the same in-memory database (tests).
        return create_async_engine(
            database_url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=10, max_overflow=5)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
