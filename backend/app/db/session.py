"""Async SQLAlchemy engine, session factory and declarative base."""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by every model."""


# `sqlalchemy_url` rewrites a managed host's postgres:// URL into the async
# form; see app/core/config.py for what it fixes and why.
async_engine = create_async_engine(
    settings.sqlalchemy_url,
    echo=False,
    future=True,
    pool_pre_ping=not settings.is_sqlite,
    connect_args=settings.db_connect_args,
    # Free hosting tiers cap connections hard, and this API runs beside a
    # seeding script and a second service. A small pool that recycles keeps the
    # whole deployment inside a 1-CPU free Postgres.
    **(
        {}
        if settings.is_sqlite
        else {"pool_size": 5, "max_overflow": 5, "pool_recycle": 280}
    ),
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_models() -> None:
    """Create every table that does not exist yet."""
    # Importing the package registers all models on Base.metadata.
    import app.models  # noqa: F401

    async with async_engine.begin() as conn:
        if settings.is_sqlite:
            from sqlalchemy import text

            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
