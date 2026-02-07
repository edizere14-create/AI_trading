from pydantic import BaseModel
from datetime import datetime
from typing import List

class StrategyRunRequest(BaseModel):
    strategy_code: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    initial_capital: float = 10_000

class Trade(BaseModel):
    timestamp: datetime
    side: str
    price: float
    size: float

class StrategyRunResult(BaseModel):
    total_return: float
    max_drawdown: float
    sharpe: float | None = None
    trades: List[Trade]
