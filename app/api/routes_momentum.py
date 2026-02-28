from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/momentum", tags=["momentum"])

momentum_worker = None
momentum_task = None
startup_error = None
fallback_is_running = False
fallback_symbol = "PI_XBTUSD"


def _build_momentum_worker(symbol: str):
    from app.services.data_service import DataService
    from engine.core.execution_engine import ExecutionEngine
    from engine.workers.momentum_worker import MomentumWorker

    execution_engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key=os.getenv("KRAKEN_API_KEY", ""),
        api_secret=os.getenv("KRAKEN_API_SECRET", ""),
        paper_mode=True,
        sandbox=True,
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
        return {
            "is_running": fallback_is_running,
            "symbol": fallback_symbol,
            "startup_error": startup_error,
            "signal_count": 0,
            "execution_count": 0,
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
        return status
    return {
        "is_running": False,
        "symbol": "PI_XBTUSD",
        "signal_count": 0,
        "execution_count": 0,
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
