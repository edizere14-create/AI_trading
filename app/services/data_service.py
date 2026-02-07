import httpx
from datetime import datetime
from app.schemas.market_data import PriceResponse
from app.core.config import settings

async def get_live_price(symbol: str) -> PriceResponse:
    """Get live crypto price from CoinGecko"""
    coingecko_id = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"
    }.get(symbol.upper(), "bitcoin")
    
    url = f"https://api.coingecko.com/api/v3/simple/price"
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
        timestamp = datetime.utcnow()
        
        return PriceResponse(
            symbol=symbol,
            price=price,
            timestamp=timestamp
        )
