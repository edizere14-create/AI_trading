from pydantic import BaseModel
from datetime import datetime

class CandleResponse(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

class PriceResponse(BaseModel):
    symbol: str
    price: float
    timestamp: datetime
