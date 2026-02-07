from pydantic import BaseModel
from datetime import datetime

class Position(BaseModel):
    id: int
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    pnl: float
    pnl_pct: float

    class Config:
        from_attributes = True

class PortfolioSummary(BaseModel):
    user_id: int
    equity: float
    cash: float
    total_value: float
    total_pnl: float
    positions: list[Position]