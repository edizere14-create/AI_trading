from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone

class CandleResponse(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

class PriceResponse(BaseModel):
    symbol: str
    price: float = Field(gt=0)
    timestamp: datetime

class Trade(BaseModel):
    timestamp: datetime
    side: str
    price: float = Field(gt=0)
    size: float = Field(gt=0)

    @field_validator("timestamp")
    @classmethod
    def ensure_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
