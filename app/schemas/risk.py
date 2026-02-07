from pydantic import BaseModel, Field

class TradeCheckRequest(BaseModel):
    symbol: str
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)

class TradeCheckResponse(BaseModel):
    approved: bool
    max_size: float
    reason: str

class RiskLimits(BaseModel):
    max_risk_per_trade: float = Field(0.02)
    max_daily_drawdown: float = Field(0.05)
    max_position_size: float = Field(0.10)
    stop_loss_pct: float = Field(0.05)

    class Config:
        from_attributes = True