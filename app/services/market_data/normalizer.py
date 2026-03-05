from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.market import Candle, Tick


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # supports sec or ms
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime.now(timezone.utc)


def normalize_candle(payload: dict[str, Any], symbol: str) -> Candle:
    return Candle(
        symbol=symbol,
        ts=_to_dt(payload.get("ts") or payload.get("timestamp")),
        open=float(payload.get("open", 0)),
        high=float(payload.get("high", 0)),
        low=float(payload.get("low", 0)),
        close=float(payload.get("close", 0)),
        volume=float(payload.get("volume", 0)),
    )


def normalize_tick(payload: dict[str, Any], symbol: str) -> Tick:
    return Tick(
        symbol=symbol,
        ts=_to_dt(payload.get("ts") or payload.get("timestamp")),
        bid=float(payload["bid"]) if payload.get("bid") is not None else None,
        ask=float(payload["ask"]) if payload.get("ask") is not None else None,
        last=float(payload["last"]) if payload.get("last") is not None else None,
        volume=float(payload["volume"]) if payload.get("volume") is not None else None,
    )