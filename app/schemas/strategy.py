from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Literal

class StrategyRunRequest(BaseModel):
    strategy_code: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    initial_capital: float = Field(default=10_000, gt=0)

class Trade(BaseModel):
    timestamp: datetime
    side: Literal["buy", "sell"]
    price: float = Field(gt=0)
    size: float = Field(gt=0)

class StrategyRunResult(BaseModel):
    total_return: float
    max_drawdown: float
    sharpe: float | None = None
    trades: List[Trade]
