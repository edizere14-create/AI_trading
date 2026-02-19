from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    start_date: datetime
    end_date: datetime
    initial_capital: float = Field(10000.0, gt=0)
    timeframe: str = Field("1d", min_length=1, max_length=10)


class BacktestResponse(BaseModel):
    symbol: str
    start_date: datetime
    end_date: datetime
    final_value: float
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    win_rate: float
    profit_factor: float
