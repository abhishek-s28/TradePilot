"""Async SQLAlchemy 2.0 setup."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.settings import get_settings


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


_settings = get_settings()

# SQLite (used in tests) needs special pool handling so that all async sessions
# share the same in-memory database. For Postgres/MySQL we use the standard pool.
_is_sqlite = _settings.database_url.startswith("sqlite")
if _is_sqlite:
    from sqlalchemy.pool import StaticPool

    _engine = create_async_engine(
        _settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
else:
    _engine = create_async_engine(
        _settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )
_SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with _SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def session_factory() -> async_sessionmaker[AsyncSession]:
    return _SessionLocal


def get_engine():
    """Return the module-level async engine. Used for startup DDL + Alembic."""
    return _engine


async def dispose_engine() -> None:
    await _engine.dispose()
