"""Technical indicators endpoints."""
from fastapi import APIRouter, HTTPException
from app.services.indicator_service import calculate_rsi, calculate_macd, calculate_bollinger_bands
from app.services.data_service import DataService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/indicators", tags=["indicators"])

data_service = DataService()


@router.get("/rsi")
async def get_rsi(symbol: str = "BTC/USD", period: int = 14):
    """Calculate RSI."""
    data = await data_service.get_ohlcv(symbol, "1h", limit=100)
    if data is None:
        raise HTTPException(status_code=500, detail="Failed to fetch data")
    rsi = calculate_rsi(data['close'], period)
    return {"symbol": symbol, "indicator": "RSI", "values": rsi.tail(10).to_dict()}


@router.get("/macd")
async def get_macd(symbol: str = "BTC/USD"):
    """Calculate MACD."""
    data = await data_service.get_ohlcv(symbol, "1h", limit=100)
    if data is None:
        raise HTTPException(status_code=500, detail="Failed to fetch data")
    macd, signal, hist = calculate_macd(data['close'])
    return {
        "symbol": symbol,
        "indicator": "MACD",
        "macd": macd.tail(10).to_dict(),
        "signal": signal.tail(10).to_dict(),
        "histogram": hist.tail(10).to_dict(),
    }


@router.get("/bollinger")
async def get_bollinger(symbol: str = "BTC/USD", period: int = 20):
    """Calculate Bollinger Bands."""
    data = await data_service.get_ohlcv(symbol, "1h", limit=100)
    if data is None:
        raise HTTPException(status_code=500, detail="Failed to fetch data")
    upper, ma, lower = calculate_bollinger_bands(data['close'], period)
    return {
        "symbol": symbol,
        "indicator": "Bollinger Bands",
        "upper": upper.tail(10).to_dict(),
        "middle": ma.tail(10).to_dict(),
        "lower": lower.tail(10).to_dict(),
    }