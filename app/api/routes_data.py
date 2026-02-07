from typing import List, Dict
import asyncio

import numpy as np
from fastapi import APIRouter, Query, HTTPException

from app.indicators.trend import fetch_kraken_ohlcv
from app.schemas.market_data import PriceResponse, CandleResponse
from app.services.data_service import get_live_price

router = APIRouter()

# --- CoinGecko Endpoints ---

@router.get("/live/{symbol}", response_model=PriceResponse)
async def get_live_price_endpoint(symbol: str) -> PriceResponse:
    """Get live crypto price from CoinGecko."""
    return await get_live_price(symbol)


# --- Kraken Endpoints ---

@router.get("/data/kraken/ohlcv/ma")
async def get_kraken_ohlcv_ma(
    symbol: str = Query("XXBTZUSD", description="Kraken pair symbol, e.g. XXBTZUSD"),
    interval: int = Query(60, description="Timeframe in minutes"),
    limit: int = Query(100, description="Number of candles to fetch"),
    window: int = Query(10, ge=1, description="Moving average window"),
) -> List[Dict[str, float]]:
    candles: List[Dict[str, float]] = await fetch_kraken_ohlcv(symbol, interval, limit=limit)
    closes = np.array([c.get("close", 0.0) for c in candles], dtype=float)

    if len(closes) < window:
        raise HTTPException(status_code=400, detail="Not enough data for moving average")

    ma = np.convolve(closes, np.ones(window, dtype=float) / window, mode="valid")
    for i, v in enumerate(ma):
        candles[i + window - 1][f"ma_{window}"] = float(v)

    return candles

@router.get("/data/kraken/ohlcv/multi")
async def get_kraken_ohlcv_multi(
    symbols: List[str] = Query(["XXBTZUSD", "XETHZUSD"], description="List of Kraken pair symbols"),
    interval: int = Query(60, description="Timeframe in minutes"),
    limit: int = Query(100, description="Number of candles to fetch"),
) -> Dict[str, List[Dict[str, float]]]:
    results = await asyncio.gather(
        *(fetch_kraken_ohlcv(s, interval, limit=limit) for s in symbols)
    )
    return dict(zip(symbols, results))

@router.get("/data/kraken/ohlcv")
async def get_kraken_ohlcv(
    symbol: str = Query("XXBTZUSD", description="Kraken pair symbol, e.g. XXBTZUSD"),
    interval: int = Query(60, description="Timeframe in minutes"),
    limit: int = Query(100, description="Number of candles to fetch"),
) -> List[Dict[str, float]]:
    candles: List[Dict[str, float]] = await fetch_kraken_ohlcv(symbol, interval, limit=limit)
    return candles
