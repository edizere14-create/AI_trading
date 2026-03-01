from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    start_date: datetime
    end_date: datetime
    initial_capital: float = Field(10000.0, gt=0)
    timeframe: str = Field("1d", min_length=1, max_length=10)


class BacktestResponse(BaseModel):
    symbol: str
    start_date: datetime
    end_date: datetime
    final_value: float
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    win_rate: float
    profit_factor: float


class EquityPoint(BaseModel):
    timestamp: str
    equity: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "timestamp": "2026-02-01T00:00:00+00:00",
                "equity": 1025.12,
            }
        }
    }


class DrawdownPoint(BaseModel):
    timestamp: str
    drawdown_pct: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "timestamp": "2026-02-01T00:00:00+00:00",
                "drawdown_pct": -2.31,
            }
        }
    }


class MonthlyPerformanceRow(BaseModel):
    month: str
    return_pct: float
    start_equity: float
    end_equity: float
    trades: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "month": "2026-01",
                "return_pct": 4.2,
                "start_equity": 1000.0,
                "end_equity": 1042.0,
                "trades": 12,
            }
        }
    }


class BacktestAnalytics(BaseModel):
    symbol: str
    timeframe: str
    days: int
    total_return_pct: float = Field(default=0.0)
    annualized_return_pct: float = Field(default=0.0)
    max_drawdown_pct: float = Field(default=0.0)
    sharpe_ratio: float = Field(default=0.0)
    win_rate_pct: float = Field(default=0.0)
    profit_factor: float = Field(default=0.0)
    trades: int = Field(default=0)
    slippage_bps: float = Field(default=0.0)
    start_equity: float = Field(default=1000.0)
    end_equity: float = Field(default=1000.0)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    drawdown_curve: list[DrawdownPoint] = Field(default_factory=list)
    monthly_performance: list[MonthlyPerformanceRow] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "symbol": "PI_XBTUSD",
                "timeframe": "1h",
                "days": 90,
                "total_return_pct": 12.4,
                "annualized_return_pct": 62.3,
                "max_drawdown_pct": -8.7,
                "sharpe_ratio": 1.15,
                "win_rate_pct": 53.4,
                "profit_factor": 1.21,
                "trades": 42,
                "slippage_bps": 2.5,
                "start_equity": 1000.0,
                "end_equity": 1124.0,
                "equity_curve": [
                    {"timestamp": "2026-02-01T00:00:00+00:00", "equity": 1025.12}
                ],
                "drawdown_curve": [
                    {"timestamp": "2026-02-01T00:00:00+00:00", "drawdown_pct": -2.31}
                ],
                "monthly_performance": [
                    {
                        "month": "2026-01",
                        "return_pct": 4.2,
                        "start_equity": 1000.0,
                        "end_equity": 1042.0,
                        "trades": 12,
                    }
                ],
            }
        }
    }


class BacktestSummaryResponse(BaseModel):
    symbol: str
    timeframe: str
    days: int

    total_return_pct: float = Field(default=0.0)
    annualized_return_pct: float = Field(default=0.0)
    max_drawdown_pct: float = Field(default=0.0)
    sharpe_ratio: float = Field(default=0.0)
    win_rate_pct: float = Field(default=0.0)
    trades: int = Field(default=0)

    start_equity: float = Field(default=1000.0)
    end_equity: float = Field(default=1000.0)

    equity_curve: list[EquityPoint] = Field(default_factory=list)
    drawdown_curve: list[DrawdownPoint] = Field(default_factory=list)
    monthly_performance: list[MonthlyPerformanceRow] = Field(default_factory=list)
    slippage_bps: float = Field(default=0.0)
    profit_factor: float = Field(default=0.0)
    analytics: BacktestAnalytics | None = Field(default=None)

    model_config = {
        "json_schema_extra": {
            "example": {
                "symbol": "PI_XBTUSD",
                "timeframe": "1h",
                "days": 90,
                "total_return_pct": 12.4,
                "annualized_return_pct": 62.3,
                "max_drawdown_pct": -8.7,
                "sharpe_ratio": 1.15,
                "win_rate_pct": 53.4,
                "trades": 42,
                "start_equity": 1000.0,
                "end_equity": 1124.0,
                "slippage_bps": 2.5,
                "profit_factor": 1.21,
                "equity_curve": [
                    {"timestamp": "2026-02-01T00:00:00+00:00", "equity": 1025.12}
                ],
                "drawdown_curve": [
                    {"timestamp": "2026-02-01T00:00:00+00:00", "drawdown_pct": -2.31}
                ],
                "monthly_performance": [
                    {
                        "month": "2026-01",
                        "return_pct": 4.2,
                        "start_equity": 1000.0,
                        "end_equity": 1042.0,
                        "trades": 12,
                    }
                ],
            }
        }
    }
