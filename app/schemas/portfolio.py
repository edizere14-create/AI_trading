from pydantic import BaseModel, Field
from datetime import datetime

class Position(BaseModel):
    id: int
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    pnl: float
    pnl_pct: float

    model_config = {"from_attributes": True}


class CollateralBalance(BaseModel):
    asset: str
    amount: float
    usd_price: float = Field(gt=0)
    haircut_pct: float = Field(default=0.0, ge=0.0, lt=1.0)
    effective_value: float

class PortfolioSummary(BaseModel):
    user_id: int
    equity: float
    cash: float
    total_value: float
    total_pnl: float
    positions: list[Position]
    collateral_balances: list[CollateralBalance] = Field(default_factory=list)
    effective_collateral_value: float = 0.0