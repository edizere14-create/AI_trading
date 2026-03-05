from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal

class PlaceOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float | None = None
    order_type: Literal["market", "limit"] = "market"
    order_kind: Literal["maker", "taker"] = "taker"
    expected_price: float | None = None

class OrderResponse(BaseModel):
    order_id: str
    status: str
    symbol: str
    side: str
    quantity: float
    price: float | None
    filled_quantity: float | None = None
    avg_fill_price: float | None = None
    slippage: float | None = None
    fill_rate: float | None = None
    latency_ms: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

class OrderHistory(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float | None
    status: str
    filled_quantity: float | None = None
    avg_fill_price: float | None = None
    slippage: float | None = None
    fill_rate: float | None = None
    latency_ms: float | None = None
    created_at: datetime
    filled_at: datetime | None = None

    model_config = {"from_attributes": True}