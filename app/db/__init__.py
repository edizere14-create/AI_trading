from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _to_async_url(db_url: str) -> str:
    value = (db_url or "").strip()
    if value.startswith("sqlite+aiosqlite://"):
        return value
    if value.startswith("sqlite://"):
        return value.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if value.startswith("postgresql+asyncpg://"):
        return value
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


ASYNC_DATABASE_URL = _to_async_url(settings.DATABASE_URL)

engine = create_async_engine(ASYNC_DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
