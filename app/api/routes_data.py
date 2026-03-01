"""Market data endpoints."""
from fastapi import APIRouter, HTTPException
from app.services.data_service import DataService
import logging
import pandas as pd

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data", tags=["data"])

data_service = DataService()


@router.get("/ohlcv")
async def get_ohlcv(symbol: str = "PF_XBTUSD", timeframe: str = "1m", limit: int = 100):
    """Get OHLCV data."""
    try:
        data = await data_service.get_ohlcv(symbol, timeframe, limit)
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
