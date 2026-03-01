"""Backtest endpoints."""
from fastapi import APIRouter, HTTPException, Query

from app.schemas.backtest import BacktestSummaryResponse, BacktestAnalytics
from app.services.backtest_service import BacktestService
from app.services.data_service import DataService

router = APIRouter(prefix="/backtest", tags=["Backtest"])


async def _summary_compat(service: BacktestService, *, days: int, symbol: str, timeframe: str) -> BacktestSummaryResponse:
    if hasattr(service, "get_summary"):
        result = await service.get_summary(days=days, symbol=symbol, timeframe=timeframe)
        if isinstance(result, BacktestSummaryResponse):
            return result
        return BacktestSummaryResponse.model_validate(result)

    if hasattr(service, "run_backtest"):
        result = await service.run_backtest(days=days, symbol=symbol, timeframe=timeframe)
        if isinstance(result, BacktestSummaryResponse):
            return result
        return BacktestSummaryResponse.model_validate(result)

    raise RuntimeError("Backtest service missing compatible summary method")


@router.get(
    "/summary",
    response_model=BacktestSummaryResponse,
    summary="Backtest summary",
    description="Runs historical simulation and returns summary metrics plus curves/tables.",
    responses={
        200: {
            "description": "Backtest summary payload",
            "content": {
                "application/json": {
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
                    }
                }
            },
        }
    },
)
async def backtest_summary(
    days: int = Query(default=90, ge=7, le=365, description="Lookback window in days", examples=[90]),
    symbol: str = Query(default="PI_XBTUSD", description="Market symbol", examples=["PI_XBTUSD"]),
    timeframe: str = Query(default="1h", description="Bar timeframe", examples=["1h"]),
) -> BacktestSummaryResponse:
    try:
        service = BacktestService(DataService())
        return await _summary_compat(service, days=days, symbol=symbol, timeframe=timeframe)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}") from exc


@router.get(
    "/analytics",
    response_model=BacktestAnalytics,
    summary="Backtest analytics",
    description="Returns structured analytics including monthly performance and drawdown curve.",
    responses={
        200: {
            "description": "Structured analytics payload",
            "content": {
                "application/json": {
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
            },
        }
    },
)
async def backtest_analytics(
    days: int = Query(default=90, ge=7, le=365, description="Lookback window in days", examples=[90]),
    symbol: str = Query(default="PI_XBTUSD", description="Market symbol", examples=["PI_XBTUSD"]),
    timeframe: str = Query(default="1h", description="Bar timeframe", examples=["1h"]),
) -> BacktestAnalytics:
    try:
        service = BacktestService(DataService())
        summary = await _summary_compat(service, days=days, symbol=symbol, timeframe=timeframe)
        if summary.analytics is not None:
            return summary.analytics

        return BacktestAnalytics(
            symbol=summary.symbol,
            timeframe=summary.timeframe,
            days=summary.days,
            total_return_pct=summary.total_return_pct,
            annualized_return_pct=summary.annualized_return_pct,
            max_drawdown_pct=summary.max_drawdown_pct,
            sharpe_ratio=summary.sharpe_ratio,
            win_rate_pct=summary.win_rate_pct,
            profit_factor=summary.profit_factor,
            trades=summary.trades,
            slippage_bps=summary.slippage_bps,
            start_equity=summary.start_equity,
            end_equity=summary.end_equity,
            equity_curve=summary.equity_curve,
            drawdown_curve=summary.drawdown_curve,
            monthly_performance=summary.monthly_performance,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest analytics failed: {exc}") from exc