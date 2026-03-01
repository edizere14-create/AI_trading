"""Market data endpoints."""
from fastapi import APIRouter, HTTPException
from app.services.data_service import DataService
import logging
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data", tags=["data"])

data_service = DataService()


def _normalize_symbol(symbol: str) -> str:
    raw = (symbol or "PF_XBTUSD").strip().upper()
    mapping = {
        "BTC/USD:USD": "PF_XBTUSD",
        "XBT/USD:USD": "PF_XBTUSD",
        "BTCUSD": "PF_XBTUSD",
        "XBTUSD": "PF_XBTUSD",
        "PI_XBTUSD": "PF_XBTUSD",
    }
    if raw in mapping:
        return mapping[raw]
    if raw.startswith("PI_"):
        return raw.replace("PI_", "PF_", 1)
    return raw


def _tf_to_kraken_interval(timeframe: str) -> str:
    tf = (timeframe or "1m").lower()
    mapping = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
        "2h": "1h",
        "3m": "1m",
    }
    return mapping.get(tf, "1m")


def _fetch_kraken_ohlcv_fallback(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    interval = _tf_to_kraken_interval(timeframe)
    now = datetime.now(timezone.utc)
    lookback_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[interval]
    start = now - timedelta(minutes=lookback_minutes * max(int(limit), 50))
    from_sec, to_sec = int(start.timestamp()), int(now.timestamp())

    requested = _normalize_symbol(symbol)
    candidates = [requested]
    if requested.startswith("PF_"):
        candidates.append(requested.replace("PF_", "PI_", 1))
    elif requested.startswith("PI_"):
        candidates.append(requested.replace("PI_", "PF_", 1))

    errors: list[str] = []
    for candidate in list(dict.fromkeys(candidates)):
        url = f"https://futures.kraken.com/api/charts/v1/trade/{candidate}/{interval}"
        try:
            response = requests.get(url, params={"from": from_sec, "to": to_sec}, timeout=10)
            response.raise_for_status()
            payload = response.json()
            candles = payload.get("candles", payload.get("result", []))
            if isinstance(candles, dict):
                candles = candles.get("candles", [])
            if not isinstance(candles, list) or not candles:
                errors.append(f"{candidate}: empty candles")
                continue

            rows: list[dict[str, float | Any]] = []
            for candle in candles[-max(int(limit), 1):]:
                if not isinstance(candle, (list, tuple, dict)):
                    continue
                if isinstance(candle, dict):
                    ts = candle.get("time", candle.get("timestamp"))
                    o = candle.get("open")
                    h = candle.get("high")
                    l = candle.get("low")
                    c = candle.get("close")
                    v = candle.get("volume", candle.get("volumeNotional", 0))
                else:
                    ts = candle[0]
                    o, h, l, c = candle[1], candle[2], candle[3], candle[4]
                    v = candle[5] if len(candle) > 5 else 0

                ts_val = pd.to_datetime(
                    ts,
                    unit="ms" if isinstance(ts, (int, float)) and ts > 10_000_000_000 else "s",
                    utc=True,
                    errors="coerce",
                )
                rows.append(
                    {
                        "timestamp": ts_val,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(v),
                    }
                )

            df = pd.DataFrame(rows).dropna(subset=["timestamp"])
            if not df.empty:
                return df.sort_values("timestamp").tail(limit).reset_index(drop=True)
            errors.append(f"{candidate}: parsed empty dataframe")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")

    raise RuntimeError("Kraken OHLCV fallback failed: " + " | ".join(errors))


@router.get("/ohlcv")
async def get_ohlcv(symbol: str = "PF_XBTUSD", timeframe: str = "1m", limit: int = 100):
    """Get OHLCV data."""
    try:
        if hasattr(data_service, "get_ohlcv"):
            try:
                data = await data_service.get_ohlcv(symbol, timeframe, limit)
            except Exception:
                data = _fetch_kraken_ohlcv_fallback(symbol=symbol, timeframe=timeframe, limit=int(limit))
        else:
            data = _fetch_kraken_ohlcv_fallback(symbol=symbol, timeframe=timeframe, limit=int(limit))
        if data is None or data.empty:
            raise RuntimeError("empty candle response")

        records = data.copy()
        records["timestamp"] = pd.to_datetime(records["timestamp"], utc=True, errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"symbol": symbol, "timeframe": timeframe, "candles": records.to_dict("records")}
    except Exception as exc:
        logger.exception("OHLCV fetch failed for %s %s: %s", symbol, timeframe, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch data")


@router.get("/kraken/ohlcv")
async def get_kraken_ohlcv(symbol: str = "PF_XBTUSD", interval: str = "1m", limit: int = 100):
    """Kraken-compatible OHLCV route for dashboard chart fallbacks."""
    return await get_ohlcv(symbol=symbol, timeframe=interval, limit=limit)


@router.get("/live/{symbol}")
async def get_live_symbol(symbol: str):
    """Get latest live price for a symbol."""
    try:
        price = await data_service.get_live_price(symbol)
        return {
            "symbol": symbol,
            "price": float(price.price),
            "timestamp": price.timestamp.isoformat(),
        }
    except Exception as exc:
        logger.exception("Live price fetch failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch live data")


@router.get("/ticker")
async def get_ticker(symbol: str = "BTC/USD"):
    """Get ticker data."""
    ticker = await data_service.get_ticker(symbol)
    if ticker is None:
        raise HTTPException(status_code=500, detail="Failed to fetch ticker")
    return ticker


@router.get("/orderbook")
async def get_orderbook(symbol: str = "BTC/USD", limit: int = 20):
    """Get order book."""
    book = await data_service.get_order_book(symbol, limit)
    if book is None:
        raise HTTPException(status_code=500, detail="Failed to fetch order book")
    return book
