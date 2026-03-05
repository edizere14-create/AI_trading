from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.api import routes_momentum
from app.models.market import Candle, Tick

router = APIRouter(prefix="/api/v1/market", tags=["market"])


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _to_ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


@router.get("/candles", response_model=list[Candle])
async def get_candles(
    symbol: str = Query(...),
    interval: str = Query("1h"),
    limit: int = Query(200, ge=1, le=5000),
):
    # 1) Prefer live worker candle cache
    worker = getattr(routes_momentum, "momentum_worker", None)
    if worker is not None:
        hist = list(getattr(worker, "candle_history", []))[-limit:]
        out: list[Candle] = []
        for c in hist:
            out.append(
                Candle(
                    symbol=symbol,
                    ts=_to_ts(c.get("timestamp") or c.get("ts")),
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=float(c.get("volume", 0)),
                )
            )
        if out:
            return out

        # 2) Fallback: pull directly from worker data_service
        ds = getattr(worker, "data_service", None)
        if ds is not None and hasattr(ds, "fetch_ohlcv"):
            raw = await _maybe_await(ds.fetch_ohlcv(symbol=symbol, timeframe=interval, limit=limit))
            out = []
            for r in raw or []:
                if isinstance(r, dict):
                    ts = r.get("timestamp") or r.get("ts")
                    o, h, l, cl, v = r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("volume", 0)
                else:
                    ts, o, h, l, cl, v = r[0], r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else 0
                out.append(
                    Candle(
                        symbol=symbol,
                        ts=_to_ts(ts),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(cl),
                        volume=float(v),
                    )
                )
            return out

    # 3) Last-resort placeholder
    now = datetime.now(timezone.utc)
    return [Candle(symbol=symbol, ts=now, open=1, high=1, low=1, close=1, volume=0)]


@router.get("/ticks", response_model=list[Tick])
async def get_ticks(symbol: str = Query(...), limit: int = Query(100, ge=1, le=5000)):
    now = datetime.now(timezone.utc)
    return [Tick(symbol=symbol, ts=now, bid=1, ask=1, last=1, volume=0) for _ in range(min(limit, 5))]