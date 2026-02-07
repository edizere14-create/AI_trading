from pydantic import BaseModel, Field
from datetime import datetime

class IndicatorRequest(BaseModel):
    symbol: str
    timeframe: str = Field(default="1h", description="Timeframe (1m, 5m, 1h, 4h, 1d)")
    period: int = Field(default=14, ge=2, description="Period for calculation")

class RSIResponse(BaseModel):
    symbol: str
    timeframe: str
    rsi: float = Field(ge=0, le=100)
    period: int
    timestamp: datetime

class MACDResponse(BaseModel):
    symbol: str
    timeframe: str
    macd: float
    signal: float
    histogram: float
    period: int
    timestamp: datetime