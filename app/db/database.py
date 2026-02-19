import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

# DATABASE_URL from env or default SQLite
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./ai_trading.db"
)

# SQLite-specific config
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    # PostgreSQL
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Dependency for FastAPI route handlers"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()