from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_table_ready = False


async def _ensure_execution_logs_table(db: AsyncSession) -> None:
    global _table_ready
    if _table_ready:
        return

    bind = db.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", "")

    if dialect == "postgresql":
        create_sql = """
        CREATE TABLE IF NOT EXISTS execution_logs (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL,
            user_id BIGINT,
            event_type VARCHAR(64) NOT NULL,
            symbol VARCHAR(64),
            side VARCHAR(16),
            quantity DOUBLE PRECISION,
            price DOUBLE PRECISION,
            status VARCHAR(32),
            details TEXT
        )
        """
    else:
        create_sql = """
        CREATE TABLE IF NOT EXISTS execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            event_type TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            quantity REAL,
            price REAL,
            status TEXT,
            details TEXT
        )
        """

    await db.execute(text(create_sql))
    _table_ready = True


async def log_execution_event(
    db: AsyncSession,
    *,
    user_id: int | None,
    event_type: str,
    symbol: str | None = None,
    side: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    status: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await _ensure_execution_logs_table(db)

    payload = json.dumps(details or {}, default=str)
    created_at = datetime.now(timezone.utc).isoformat()

    await db.execute(
        text(
            """
            INSERT INTO execution_logs (
                created_at, user_id, event_type, symbol, side, quantity, price, status, details
            ) VALUES (
                :created_at, :user_id, :event_type, :symbol, :side, :quantity, :price, :status, :details
            )
            """
        ),
        {
            "created_at": created_at,
            "user_id": user_id,
            "event_type": event_type,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "status": status,
            "details": payload,
        },
    )
