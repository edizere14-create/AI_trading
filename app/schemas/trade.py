from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal

class PlaceOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float | None = None
    order_type: Literal["market", "limit"] = "market"

class OrderResponse(BaseModel):
    order_id: str
    status: str
    symbol: str
    side: str
    quantity: float
    price: float | None
    created_at: datetime

    class Config:
        from_attributes = True

class OrderHistory(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float | None
    status: str
    created_at: datetime
    filled_at: datetime | None = None

    class Config:
        from_attributes = True