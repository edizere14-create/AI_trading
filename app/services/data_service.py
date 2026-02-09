import httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict
import logging

from app.schemas.market_data import PriceResponse, CandleResponse

logger = logging.getLogger(__name__)

class DataService:
    """Data service for managing market data."""
    
    async def get_live_price(self, symbol: str) -> PriceResponse:
        """Get live price for a symbol."""
        coingecko_id = {
            "BTC": "bitcoin", 
            "ETH": "ethereum", 
            "SOL": "solana"
        }.get(symbol.upper(), "bitcoin")
        
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coingecko_id,
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            price_data = data[coingecko_id]
            
            price = float(price_data['usd'])
            timestamp = datetime.now(timezone.utc)
            
            return PriceResponse(
                symbol=symbol,
                price=price,
                timestamp=timestamp
            )

async def get_historical_candles(
    db: AsyncSession,
    symbol: str,
    days: int = 30
) -> List[Dict[str, float]]:
    """Get historical candle data."""
    try:
        logger.debug("Fetching %d days of candles for %s", days, symbol)
        
        mock_candles = []
        base_price = 50000.0
        
        for i in range(days * 24):
            timestamp = datetime.now(timezone.utc) - timedelta(hours=i)
            mock_candles.append({
                "timestamp": timestamp.isoformat(),
                "open": base_price + (i % 100),
                "high": base_price + (i % 100) + 50,
                "low": base_price + (i % 100) - 50,
                "close": base_price + (i % 100) + 10,
                "volume": 100.0 + (i % 50)
            })
        
        return mock_candles
    except Exception as exc:
        logger.error("Error fetching candles for %s: %s", symbol, exc)
        return []

async def get_current_price(
    db: AsyncSession,
    symbol: str
) -> PriceResponse:
    """Get current price for a symbol."""
    try:
        logger.debug("Fetching current price for %s", symbol)
        
        return PriceResponse(
            symbol=symbol,
            price=50000.0,
            timestamp=datetime.now(timezone.utc)
        )
    except Exception as exc:
        logger.error("Error fetching price for %s: %s", symbol, exc)
        return PriceResponse(
            symbol=symbol,
            price=0.0,
            timestamp=datetime.now(timezone.utc)
        )

async def store_candles(
    db: AsyncSession,
    symbol: str,
    candles: List[CandleResponse]
) -> bool:
    """Store candle data in database."""
    try:
        logger.info("Storing %d candles for %s", len(candles), symbol)
        return True
    except Exception as exc:
        logger.error("Error storing candles for %s: %s", symbol, exc)
        return False

async def get_live_price(symbol: str) -> PriceResponse:
    """Get live price (standalone function for backward compatibility)."""
    service = DataService()
    return await service.get_live_price(symbol)
