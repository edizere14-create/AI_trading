from __future__ import annotations

import asyncio
import inspect
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _json_safe(tolist())
        except Exception:
            pass

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass

    return str(value)


def _symbol_key(value: str) -> str:
    raw = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    if raw.startswith("PI"):
        raw = raw[2:]
    if raw.startswith("PF"):
        raw = raw[2:]
    raw = raw.replace("XBT", "BTC")
    return raw


def _symbols_match(a: str, b: str) -> bool:
    ka = _symbol_key(a)
    kb = _symbol_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka


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


async def _fetch_exchange_order_state(worker: Any) -> dict[str, Any]:
    execution_engine = getattr(worker, "execution_engine", None)
    if execution_engine is None:
        return {"mode": "paper", "open_orders": [], "positions": [], "errors": []}

    if getattr(execution_engine, "paper_mode", True):
        return {"mode": "paper", "open_orders": [], "positions": [], "errors": []}

    exchange = getattr(execution_engine, "exchange", None)
    if exchange is None:
        return {"mode": "live", "open_orders": [], "positions": [], "errors": ["exchange_not_initialized"]}

    symbol_hint = str(getattr(worker, "symbol", "") or "")

    def _sync_fetch() -> dict[str, Any]:
        errors: list[str] = []
        open_orders: list[dict[str, Any]] = []
        positions: list[dict[str, Any]] = []

        try:
            rows = exchange.fetch_open_orders()
            if isinstance(rows, list):
                open_orders = rows
        except Exception as exc:
            errors.append(f"fetch_open_orders:{exc}")
            if symbol_hint:
                try:
                    rows = exchange.fetch_open_orders(symbol_hint)
                    if isinstance(rows, list):
                        open_orders = rows
                except Exception as exc2:
                    errors.append(f"fetch_open_orders(symbol):{exc2}")

        try:
            rows = exchange.fetch_positions()
            if isinstance(rows, list):
                positions = rows
        except Exception as exc:
            errors.append(f"fetch_positions:{exc}")
            if symbol_hint:
                try:
                    rows = exchange.fetch_positions([symbol_hint])
                    if isinstance(rows, list):
                        positions = rows
                except Exception as exc2:
                    errors.append(f"fetch_positions(symbol):{exc2}")

        return {"mode": "live", "open_orders": open_orders, "positions": positions, "errors": errors}

    return await asyncio.to_thread(_sync_fetch)


def _position_lookup(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        qty = _safe_float(pos.get("contracts"), 0.0)
        if qty == 0:
            continue
        symbol = str(pos.get("symbol") or pos.get("id") or "")
        if not symbol:
            continue
        out[_symbol_key(symbol)] = pos
    return out


def _open_order_lookup(open_orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in open_orders:
        if not isinstance(row, dict):
            continue
        order_id = str(row.get("id") or "").strip()
        if order_id:
            out[order_id] = row
    return out


def _coerce_candles_df(payload: Any) -> pd.DataFrame:
    if isinstance(payload, pd.DataFrame):
        return payload
    if isinstance(payload, dict):
        candidate = payload.get("candles", payload.get("result", payload.get("data")))
        if isinstance(candidate, list):
            payload = candidate
    if isinstance(payload, list):
        df = pd.DataFrame(payload)
        if not df.empty:
            return df
    return pd.DataFrame()


async def _load_candles_compatible(
    data_service: Any,
    *,
    symbol: str,
    timeframe: str,
    limit: int,
    exchange_id: str,
) -> pd.DataFrame:
    get_ohlcv = getattr(data_service, "get_ohlcv", None)
    if callable(get_ohlcv):
        try:
            result = get_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit, exchange_id=exchange_id)
        except TypeError:
            result = get_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        if inspect.isawaitable(result):
            result = await result
        return _coerce_candles_df(result)

    fetch_ohlcv = getattr(data_service, "fetch_ohlcv", None)
    if callable(fetch_ohlcv):
        try:
            result = fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit, exchange_id=exchange_id)
        except TypeError:
            result = fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        if inspect.isawaitable(result):
            result = await result
        return _coerce_candles_df(result)

    sync_fetch = getattr(data_service, "_fetch_kraken_ohlcv_sync", None)
    if callable(sync_fetch):
        result = await asyncio.to_thread(sync_fetch, symbol, timeframe, limit)
        return _coerce_candles_df(result)

    raise AttributeError("DataService missing get_ohlcv/fetch_ohlcv compatibility methods")


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
        safe_status: dict[str, Any] = {
            "is_running": bool(status.get("is_running", False)),
            "symbol": str(status.get("symbol", fallback_symbol) or fallback_symbol),
            "signal_count": int(_safe_float(status.get("signal_count", 0), 0.0)),
            "execution_count": int(_safe_float(status.get("execution_count", 0), 0.0)),
            "interval": str(status.get("interval", "") or ""),
            "last_decision_reason": str(status.get("last_decision_reason", "") or ""),
            "startup_error": startup_error,
        }
        if isinstance(status.get("risk"), dict):
            safe_status["risk"] = _json_safe(status.get("risk"))
        else:
            safe_status["risk"] = {
                "account_balance": 0.0,
                "drawdown_pct": 0.0,
                "daily_loss": 0.0,
                "total_pnl": 0.0,
                "open_positions": 0,
            }
        if isinstance(status.get("last_signal"), dict):
            safe_status["last_signal"] = _json_safe(status.get("last_signal"))

        safe_status["ai"] = ai
        safe_status["analytics"] = ai
        safe_status["confidence"] = ai.get("confidence", 0.0)
        safe_status["bias"] = ai.get("bias", "NEUTRAL")
        price = _latest_worker_price()
        if price is not None:
            safe_status["last_price"] = price
        return safe_status

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


@router.get("/orders-sync")
async def get_momentum_orders_sync(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    global momentum_worker
    if momentum_worker is None:
        return {
            "status": "unavailable",
            "reason": "momentum_worker_not_initialized",
            "orders": [],
            "open_orders_count": 0,
            "open_positions_count": 0,
        }

    history = list(getattr(momentum_worker, "signal_history", []))
    recent = [row for row in history if isinstance(row, dict)][-limit:]
    exchange_state = await _fetch_exchange_order_state(momentum_worker)
    open_orders = exchange_state.get("open_orders", [])
    positions = exchange_state.get("positions", [])
    open_orders_by_id = _open_order_lookup(open_orders if isinstance(open_orders, list) else [])
    positions_by_symbol = _position_lookup(positions if isinstance(positions, list) else [])

    rows: list[dict[str, Any]] = []
    for row in reversed(recent):
        order_id = str(row.get("order_id") or "").strip()
        symbol = str(row.get("symbol") or "")
        side = str(row.get("side") or "").lower()
        local_status = str(row.get("status") or "").lower()
        key = _symbol_key(symbol)
        position = positions_by_symbol.get(key)
        if position is None and symbol and isinstance(positions, list):
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                pos_symbol = str(pos.get("symbol") or pos.get("id") or "")
                if _symbols_match(symbol, pos_symbol):
                    position = pos
                    break
        has_position = position is not None
        exchange_order = open_orders_by_id.get(order_id) if order_id else None

        progress_state = "unknown"
        exchange_status = None
        amount = None
        remaining = None
        filled_qty = None

        if isinstance(exchange_order, dict):
            exchange_status = str(exchange_order.get("status") or "open").lower()
            amount = _safe_float(exchange_order.get("amount"), 0.0)
            remaining = _safe_float(exchange_order.get("remaining"), 0.0)
            filled_qty = max(0.0, amount - remaining) if amount > 0 else _safe_float(exchange_order.get("filled"), 0.0)
            if filled_qty > 0 and remaining > 0:
                progress_state = "partially_filled_open"
            else:
                progress_state = "open"
        elif has_position:
            progress_state = "filled_or_partially_filled"
        elif local_status in {"filled", "partial"}:
            progress_state = "filled_or_partially_filled"
        elif local_status in {"cancelled", "canceled", "rejected", "expired"}:
            progress_state = "closed_or_canceled"
        elif local_status in {"submitted", "pending", "open"}:
            progress_state = "closed_or_canceled"

        rows.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "timestamp": row.get("timestamp"),
                "local_status": local_status or None,
                "exchange_status": exchange_status,
                "progress_state": progress_state,
                "filled_quantity": filled_qty,
                "remaining_quantity": remaining,
                "amount": amount,
                "has_open_position": has_position,
                "position_symbol": (
                    str(position.get("symbol") or position.get("id") or "")
                    if isinstance(position, dict)
                    else None
                ),
                "position_contracts": (
                    _safe_float(position.get("contracts"), 0.0)
                    if isinstance(position, dict)
                    else 0.0
                ),
            }
        )

    return {
        "status": "ok",
        "mode": exchange_state.get("mode"),
        "errors": exchange_state.get("errors", []),
        "open_orders_count": len(open_orders if isinstance(open_orders, list) else []),
        "open_positions_count": len([p for p in (positions if isinstance(positions, list) else []) if _safe_float(p.get("contracts"), 0.0) != 0.0]),
        "orders": rows,
    }


@router.get("/analytics")
async def get_momentum_analytics(symbol: str = Query("PI_XBTUSD")) -> dict[str, Any]:
    global momentum_worker
    exchange_id = os.getenv("MARKET_DATA_EXCHANGE_ID", "krakenfutures")

    if momentum_worker is not None:
        return _worker_analytics()

    try:
        data_service = _build_data_service(exchange_id)
        candles = await _load_candles_compatible(
            data_service,
            symbol=symbol,
            timeframe="1h",
            limit=120,
            exchange_id=exchange_id,
        )
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
        ohlcv = await _load_candles_compatible(
            data_service,
            symbol=symbol,
            timeframe="1h",
            limit=5,
            exchange_id=exchange_id,
        )
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
