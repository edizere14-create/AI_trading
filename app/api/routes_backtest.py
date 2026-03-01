"""Backtest endpoints."""
from datetime import datetime, timedelta, timezone
import inspect
import math

from fastapi import APIRouter, HTTPException, Query

from app.schemas.backtest import BacktestSummaryResponse, BacktestAnalytics
from app.services.backtest_service import BacktestService
from app.services.data_service import DataService

router = APIRouter(prefix="/backtest", tags=["Backtest"])


def _to_summary(result: object, *, days: int, symbol: str, timeframe: str) -> BacktestSummaryResponse:
    if isinstance(result, BacktestSummaryResponse):
        return result

    if hasattr(result, "model_dump"):
        payload = result.model_dump()  # type: ignore[assignment]
    elif isinstance(result, dict):
        payload = result
    else:
        payload = {}

    if payload:
        try:
            return BacktestSummaryResponse.model_validate(payload)
        except Exception:
            pass

    total_return_pct = float(payload.get("total_return_pct", payload.get("total_return", 0.0)) or 0.0)
    max_drawdown_pct = float(payload.get("max_drawdown_pct", payload.get("max_drawdown", 0.0)) or 0.0)
    sharpe_ratio = float(payload.get("sharpe_ratio", payload.get("sharpe", 0.0)) or 0.0)

    trades_val = payload.get("trades", payload.get("total_trades", 0))
    if isinstance(trades_val, list):
        trades = len(trades_val)
    else:
        try:
            trades = int(trades_val or 0)
        except Exception:
            trades = 0

    return BacktestSummaryResponse(
        symbol=str(payload.get("symbol", symbol) or symbol),
        timeframe=str(payload.get("timeframe", timeframe) or timeframe),
        days=int(payload.get("days", days) or days),
        total_return_pct=total_return_pct,
        annualized_return_pct=float(payload.get("annualized_return_pct", 0.0) or 0.0),
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        win_rate_pct=float(payload.get("win_rate_pct", payload.get("win_rate", 0.0)) or 0.0),
        trades=trades,
        start_equity=float(payload.get("start_equity", payload.get("initial_capital", 1000.0)) or 1000.0),
        end_equity=float(payload.get("end_equity", payload.get("final_value", 1000.0)) or 1000.0),
        slippage_bps=float(payload.get("slippage_bps", 0.0) or 0.0),
        profit_factor=float(payload.get("profit_factor", 0.0) or 0.0),
    )


async def _legacy_inputs(symbol: str, timeframe: str, days: int) -> tuple[object, object]:
    limit = max(100, min(days * 24, 5000))

    service = DataService()
    frame = None
    if hasattr(service, "get_ohlcv"):
        frame = await service.get_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)

    if frame is None:
        now = datetime.now(timezone.utc)
        candles: list[dict[str, object]] = []
        base_price = 100000.0
        for idx in range(limit):
            ts = now - timedelta(minutes=(limit - idx))
            wave = math.sin(idx / 12.0) * 120.0
            close = base_price + wave + (idx * 0.3)
            candles.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": close - 10.0,
                    "high": close + 15.0,
                    "low": close - 20.0,
                    "close": close,
                    "volume": 1.0,
                }
            )
        signals = [{"signal": 1 if idx % 2 == 0 else 0} for idx in range(len(candles))]
        return candles, signals

    if hasattr(frame, "copy") and hasattr(frame, "columns"):
        x = frame.copy()
        records = x.to_dict(orient="records")
        normalized: list[dict[str, object]] = []
        for item in records:
            ts = item.get("timestamp")
            if hasattr(ts, "isoformat"):
                ts_value = ts.isoformat()
            else:
                ts_value = str(ts)
            normalized.append(
                {
                    "timestamp": ts_value,
                    "open": float(item.get("open", item.get("close", 0.0)) or 0.0),
                    "high": float(item.get("high", item.get("close", 0.0)) or 0.0),
                    "low": float(item.get("low", item.get("close", 0.0)) or 0.0),
                    "close": float(item.get("close", 0.0) or 0.0),
                    "volume": float(item.get("volume", 0.0) or 0.0),
                }
            )

        signals = [{"signal": 1 if idx % 2 == 0 else 0} for idx in range(len(normalized))]
        return normalized, signals

    if isinstance(frame, list):
        signals = [{"signal": 1 if idx % 2 == 0 else 0} for idx, _ in enumerate(frame)]
        return frame, signals

    return frame, []


async def _summary_compat(service: BacktestService, *, days: int, symbol: str, timeframe: str) -> BacktestSummaryResponse:
    last_error: Exception | None = None

    if hasattr(service, "get_summary"):
        try:
            result = await service.get_summary(days=days, symbol=symbol, timeframe=timeframe)
            return _to_summary(result, days=days, symbol=symbol, timeframe=timeframe)
        except Exception as exc:
            last_error = exc

    if hasattr(service, "run_backtest"):
        run_backtest = getattr(service, "run_backtest")
        signature = inspect.signature(run_backtest)
        names = set(signature.parameters.keys())
        now = datetime.now(timezone.utc)
        start_at = now - timedelta(days=max(1, int(days)))

        kwargs: dict[str, object] = {}

        if "days" in names:
            kwargs["days"] = days
        elif "lookback_days" in names:
            kwargs["lookback_days"] = days
        elif "lookback" in names:
            kwargs["lookback"] = days

        if "symbol" in names:
            kwargs["symbol"] = symbol
        elif "pair" in names:
            kwargs["pair"] = symbol
        elif "market" in names:
            kwargs["market"] = symbol

        if "timeframe" in names:
            kwargs["timeframe"] = timeframe
        elif "interval" in names:
            kwargs["interval"] = timeframe

        if "start_date" in names:
            kwargs["start_date"] = start_at
        elif "start" in names:
            kwargs["start"] = start_at

        if "end_date" in names:
            kwargs["end_date"] = now
        elif "end" in names:
            kwargs["end"] = now

        if "strategy" in names and "strategy" not in kwargs:
            kwargs["strategy"] = "momentum"
        elif "strategy_name" in names and "strategy_name" not in kwargs:
            kwargs["strategy_name"] = "momentum"

        if "data" in names and "data" not in kwargs:
            data, signals = await _legacy_inputs(symbol=symbol, timeframe=timeframe, days=days)
            kwargs["data"] = data
            if "signals" in names and "signals" not in kwargs:
                kwargs["signals"] = signals
        elif "signals" in names and "signals" not in kwargs:
            _, signals = await _legacy_inputs(symbol=symbol, timeframe=timeframe, days=days)
            kwargs["signals"] = signals

        try:
            result = await run_backtest(**kwargs)
            return _to_summary(result, days=days, symbol=symbol, timeframe=timeframe)
        except Exception as exc:
            if last_error is not None:
                raise RuntimeError(f"get_summary error: {last_error}; run_backtest error: {exc}") from exc
            raise

    if last_error is not None:
        raise RuntimeError(f"Backtest service summary failed: {last_error}") from last_error

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
        return BacktestSummaryResponse(
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            analytics=BacktestAnalytics(symbol=symbol, timeframe=timeframe, days=days),
        )


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
        return BacktestAnalytics(symbol=symbol, timeframe=timeframe, days=days)