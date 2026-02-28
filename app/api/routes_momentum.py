from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/momentum", tags=["momentum"])

momentum_worker = None


@router.post("/start")
async def start_momentum(symbol: str | None = None) -> dict[str, Any]:
    global momentum_worker
    if momentum_worker is None:
        return {"status": "unavailable", "detail": "Momentum worker not initialized", "symbol": symbol or "PI_XBTUSD"}

    if symbol and hasattr(momentum_worker, "symbol"):
        momentum_worker.symbol = symbol

    if hasattr(momentum_worker, "start"):
        result = momentum_worker.start()
        if hasattr(result, "__await__"):
            await result

    return {"status": "started", "symbol": getattr(momentum_worker, "symbol", symbol or "PI_XBTUSD")}


@router.post("/stop")
async def stop_momentum() -> dict[str, Any]:
    global momentum_worker
    if momentum_worker is None:
        return {"status": "unavailable", "detail": "Momentum worker not initialized"}

    if hasattr(momentum_worker, "stop"):
        result = momentum_worker.stop()
        if hasattr(result, "__await__"):
            await result

    return {"status": "stopped"}


@router.get("/status")
async def get_momentum_status() -> dict[str, Any]:
    global momentum_worker
    if momentum_worker is None:
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
