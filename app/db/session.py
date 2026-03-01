"""Database session management."""
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from configs.settings import settings


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

# Sync engine for blocking operations
engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Convert sync URL to async
async_db_url = _to_async_url(settings.DATABASE_URL)

async_engine = create_async_engine(
    async_db_url,
    echo=False,
    future=True,
)

AsyncSessionLocal = sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


def get_db():
    """Sync DB dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Async DB dependency."""
    async with AsyncSessionLocal() as session:
        yield session