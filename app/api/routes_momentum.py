from __future__ import annotations

import asyncio
import os
import logging
import traceback
import math
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Query
import pandas as pd

router = APIRouter(prefix="/momentum", tags=["momentum"])
logger = logging.getLogger(__name__)

momentum_worker = None
momentum_task = None
startup_error = None
fallback_is_running = False
fallback_symbol = "PI_XBTUSD"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _fallback_analytics(reason: str = "Waiting for market data...") -> dict[str, Any]:
    return {
        "bias": "NEUTRAL",
        "confidence": None,
        "vol_forecast": None,
        "pattern_summary": None,
        "why_trade": reason,
        "signals": [],
    }


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest_worker_price() -> float | None:
    global momentum_worker
    if momentum_worker is None:
        return None

    try:
        candles = list(getattr(momentum_worker, "candle_history", []))
    except Exception:
        candles = []
    if candles:
        last = candles[-1]
        if isinstance(last, dict):
            price = _finite_or_none(last.get("close"))
            if price is not None and price > 0:
                return price

    signal = getattr(momentum_worker, "last_signal", None)
    if isinstance(signal, dict):
        price = _finite_or_none(signal.get("price"))
        if price is not None and price > 0:
            return price

    return None


def _worker_signals(limit: int = 50) -> list[dict[str, Any]]:
    global momentum_worker
    if momentum_worker is None:
        return []

    raw = list(getattr(momentum_worker, "signal_history", []))[-limit:]
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side", row.get("action", "")) or "").strip().lower()
        if side not in {"buy", "sell"}:
            continue
        out.append(
            {
                "side": side,
                "timestamp": row.get("timestamp"),
                "price": _finite_or_none(
                    row.get("avg_fill_price", row.get("price", row.get("entry_price")))
                ),
            }
        )
    return out


def _worker_analytics() -> dict[str, Any]:
    global momentum_worker
    if momentum_worker is None:
        return _fallback_analytics()

    last_signal = getattr(momentum_worker, "last_signal", None)
    if not isinstance(last_signal, dict):
        return {
            "bias": "NEUTRAL",
            "confidence": 0.0,
            "vol_forecast": None,
            "pattern_summary": "Worker warmup",
            "why_trade": "Momentum worker is running and waiting for first signal.",
            "signals": _worker_signals(),
        }

    momentum_value = _finite_or_none(last_signal.get("momentum")) or 0.0
    side = str(last_signal.get("side", last_signal.get("action", "neutral")) or "").strip().lower()

    if side == "buy":
        bias = "BUY"
    elif side == "sell":
        bias = "SELL"
    else:
        bias = "NEUTRAL"

    confidence = min(99.0, max(5.0, abs(momentum_value) * 20.0))

    try:
        candles = pd.DataFrame(list(getattr(momentum_worker, "candle_history", [])))
        close = pd.to_numeric(candles.get("close"), errors="coerce").dropna()
        if len(close) >= 20:
            vol_forecast = _finite_or_none(close.pct_change().dropna().tail(60).std() * (24 * 365) ** 0.5)
        else:
            vol_forecast = None
    except Exception:
        vol_forecast = None

    signal_price = _finite_or_none(last_signal.get("price"))
    price_text = f" price={signal_price:.2f}" if signal_price is not None else ""
    pattern_summary = f"Worker signal: {side or 'neutral'} momentum={momentum_value:.3f}%{price_text}"
    why_trade = "Derived from running MomentumWorker signal stream."

    return {
        "bias": bias,
        "confidence": _finite_or_none(confidence),
        "vol_forecast": vol_forecast,
        "pattern_summary": pattern_summary,
        "why_trade": why_trade,
        "signals": _worker_signals(),
    }


def _build_data_service(exchange_id: str):
    from app.services.data_service import DataService

    try:
        return DataService(exchange_id=exchange_id)
    except TypeError:
        return DataService()


def _build_momentum_worker(symbol: str):
    from app.services.data_service import DataService
    from engine.core.execution_engine import ExecutionEngine
    from engine.workers.momentum_worker import MomentumWorker

    paper_mode = _env_bool("TRADING_PAPER_MODE", True)
    sandbox_mode = _env_bool("KRAKEN_FUTURES_DEMO", True)

    execution_engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key=os.getenv("KRAKEN_API_KEY", ""),
        api_secret=os.getenv("KRAKEN_API_SECRET", ""),
        paper_mode=paper_mode,
        sandbox=sandbox_mode,
    )
    data_service = DataService()
    return MomentumWorker(
        symbol=symbol,
        interval=os.getenv("MOMENTUM_INTERVAL", "1m"),
        execution_engine=execution_engine,
        data_service=data_service,
        momentum_period=int(os.getenv("MOMENTUM_PERIOD", "14")),
        buy_threshold=float(os.getenv("MOMENTUM_BUY_THRESHOLD", "0.01")),
        sell_threshold=float(os.getenv("MOMENTUM_SELL_THRESHOLD", "-0.01")),
        account_balance=float(os.getenv("MOMENTUM_ACCOUNT_BALANCE", "1000")),
    )


@router.post("/start")
async def start_momentum(symbol: str | None = None) -> dict[str, Any]:
    global momentum_worker, momentum_task, startup_error, fallback_is_running, fallback_symbol
    if symbol:
        fallback_symbol = symbol

    if momentum_worker is None:
        try:
            momentum_worker = _build_momentum_worker(fallback_symbol)
            startup_error = None
        except Exception as exc:
            startup_error = str(exc)
            fallback_is_running = True
            return {
                "status": "started",
                "symbol": fallback_symbol,
                "mode": "fallback",
                "startup_error": startup_error,
            }

    if symbol and hasattr(momentum_worker, "symbol"):
        momentum_worker.symbol = symbol

    if momentum_task and not momentum_task.done():
        return {"status": "already_running", "symbol": getattr(momentum_worker, "symbol", fallback_symbol)}

    if hasattr(momentum_worker, "start"):
        momentum_task = asyncio.create_task(momentum_worker.start())

    return {"status": "started", "symbol": getattr(momentum_worker, "symbol", fallback_symbol)}


@router.post("/stop")
async def stop_momentum() -> dict[str, Any]:
    global momentum_worker, momentum_task, fallback_is_running
    if momentum_worker is None:
        fallback_is_running = False
        return {"status": "stopped", "mode": "fallback"}

    if hasattr(momentum_worker, "stop"):
        await momentum_worker.stop()
    if momentum_task and not momentum_task.done():
        momentum_task.cancel()
        with suppress(asyncio.CancelledError):
            await momentum_task
    momentum_task = None

    return {"status": "stopped"}


@router.get("/status")
async def get_momentum_status() -> dict[str, Any]:
    global momentum_worker, startup_error, fallback_is_running, fallback_symbol
    if momentum_worker is None:
        ai = _fallback_analytics("Momentum worker not initialized.")
        return {
            "is_running": fallback_is_running,
            "symbol": fallback_symbol,
            "startup_error": startup_error,
            "signal_count": 0,
            "execution_count": 0,
            "ai": ai,
            "analytics": ai,
            "risk": {
                "account_balance": 0.0,
                "drawdown_pct": 0.0,
                "daily_loss": 0.0,
                "total_pnl": 0.0,
                "open_positions": 0,
            },
        }

    status = momentum_worker.get_status()
    if isinstance(status, dict):
        ai = _worker_analytics()
        status["ai"] = ai
        status["analytics"] = ai
        status["confidence"] = ai.get("confidence", 0.0)
        status["bias"] = ai.get("bias", "NEUTRAL")
        price = _latest_worker_price()
        if price is not None:
            status["last_price"] = price
        return status

    ai = _fallback_analytics("Momentum status unavailable.")
    return {
        "is_running": False,
        "symbol": "PI_XBTUSD",
        "signal_count": 0,
        "execution_count": 0,
        "ai": ai,
        "analytics": ai,
        "risk": {
            "account_balance": 0.0,
            "drawdown_pct": 0.0,
            "daily_loss": 0.0,
            "total_pnl": 0.0,
            "open_positions": 0,
        },
    }


@router.get("/history")
async def get_momentum_history(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    global momentum_worker
    if momentum_worker is None:
        return {"signals": [], "candles": []}

    signals = list(getattr(momentum_worker, "signal_history", []))[-limit:]
    candles = list(getattr(momentum_worker, "candle_history", []))[-limit:]
    return {"signals": signals, "candles": candles}


@router.get("/analytics")
async def get_momentum_analytics(symbol: str = Query("PI_XBTUSD")) -> dict[str, Any]:
    global momentum_worker
    exchange_id = os.getenv("MARKET_DATA_EXCHANGE_ID", "krakenfutures")

    if momentum_worker is not None:
        return _worker_analytics()

    try:
        data_service = _build_data_service(exchange_id)
        try:
            candles = await data_service.get_ohlcv(
                symbol=symbol,
                timeframe="1h",
                limit=120,
                exchange_id=exchange_id,
            )
        except TypeError:
            candles = await data_service.get_ohlcv(symbol=symbol, timeframe="1h", limit=120)
        if candles is None or candles.empty:
            return _fallback_analytics("No market data returned.")

        close = pd.to_numeric(candles.get("close"), errors="coerce").dropna()
        if len(close) < 30:
            return _fallback_analytics("Insufficient candles for analytics.")

        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
        momentum10 = float((close.iloc[-1] / close.iloc[-11] - 1.0) * 100.0) if len(close) >= 11 else 0.0
        trend = (sma20 / sma50 - 1.0) * 100.0 if sma50 else 0.0
        score = trend + momentum10

        if score > 0.05:
            bias = "BUY"
            pattern_summary = "Bullish momentum with price above trend mean"
            signals = ["momentum_bull", "trend_confirmed"]
        elif score < -0.05:
            bias = "SELL"
            pattern_summary = "Bearish momentum with price below trend mean"
            signals = ["momentum_bear", "trend_confirmed"]
        else:
            bias = "NEUTRAL"
            pattern_summary = "Mixed momentum and trend"
            signals = []

        confidence = _finite_or_none(min(99.0, max(5.0, abs(score) * 250.0)))
        vol_forecast = _finite_or_none(close.pct_change().dropna().tail(60).std() * (24 * 365) ** 0.5)

        trend_fmt = _finite_or_none(trend)
        momentum_fmt = _finite_or_none(momentum10)
        why_trade = "Market signal computed from trend/momentum."
        if trend_fmt is not None and momentum_fmt is not None:
            why_trade = f"trend={trend_fmt:.3f}% and momentum10={momentum_fmt:.3f}%"

        return {
            "bias": bias,
            "confidence": confidence,
            "vol_forecast": vol_forecast,
            "pattern_summary": pattern_summary,
            "why_trade": why_trade,
            "signals": signals,
        }
    except Exception as exc:
        logger.warning("Momentum analytics fetch failed; using fallback: %s", exc)
        return _fallback_analytics("Waiting for market data...")


@router.get("/debug-data")
async def debug_data(symbol: str = Query("PF_XBTUSD")) -> dict[str, Any]:
    exchange_id = os.getenv("MARKET_DATA_EXCHANGE_ID", "krakenfutures")

    try:
        data_service = _build_data_service(exchange_id)

        if hasattr(data_service, "get_ohlcv"):
            try:
                ohlcv = await data_service.get_ohlcv(symbol=symbol, timeframe="1h", limit=5, exchange_id=exchange_id)
            except TypeError:
                ohlcv = await data_service.get_ohlcv(symbol=symbol, timeframe="1h", limit=5)
        else:
            return {
                "status": "degraded",
                "symbol": symbol,
                "rows": 0,
                "latest_close": None,
                "warning": "DataService missing get_ohlcv",
            }
        latest_close: float | None = None
        if ohlcv is not None and not ohlcv.empty and "close" in ohlcv.columns:
            close_series = pd.to_numeric(ohlcv["close"], errors="coerce").dropna()
            if not close_series.empty:
                latest_close = _finite_or_none(close_series.iloc[-1])
        return {
            "status": "ok",
            "symbol": symbol,
            "rows": int(len(ohlcv) if ohlcv is not None else 0),
            "latest_close": latest_close,
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "symbol": symbol,
        }
