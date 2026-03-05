from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class Candle(BaseModel):
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Tick(BaseModel):
    symbol: str
    ts: datetime
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: float | None = None


class MarketQuery(BaseModel):
    symbol: str = Field(..., min_length=3)
    interval: str = "1h"
    limit: int = Field(100, ge=1, le=5000)