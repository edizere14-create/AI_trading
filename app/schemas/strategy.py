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


class TimeframeSignal(BaseModel):
    timeframe: str
    momentum: float
    trend_strength: float
    volatility: float
    score: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "timeframe": "1h",
                "momentum": 1.21,
                "trend_strength": 0.84,
                "volatility": 18.7,
                "score": 11.9,
            }
        }
    }


class StrategySignalResponse(BaseModel):
    symbol: str
    mode: Literal["conservative", "balanced", "aggressive"]
    action: Literal["buy", "sell", "hold"]
    confidence: float
    aggressiveness: float
    suggested_size_pct: float
    multi_timeframe: List[TimeframeSignal] = Field(default_factory=list)
    metadata: dict[str, float | str] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "example": {
                "symbol": "PI_XBTUSD",
                "mode": "balanced",
                "action": "buy",
                "confidence": 0.72,
                "aggressiveness": 0.94,
                "suggested_size_pct": 0.0135,
                "multi_timeframe": [
                    {
                        "timeframe": "1h",
                        "momentum": 1.21,
                        "trend_strength": 0.84,
                        "volatility": 18.7,
                        "score": 11.9,
                    }
                ],
                "metadata": {
                    "aggregate_score": 14.5,
                    "average_volatility": 17.0,
                },
            }
        }
    }
