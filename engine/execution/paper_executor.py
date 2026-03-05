from datetime import datetime, timezone
import time
import logging
import aiohttp

logger = logging.getLogger(__name__)

class PaperExecutor:
    def __init__(self, data_service=None):
        self.data_service = data_service
        self.orders = {}
    
    async def execute(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Execute a paper trade."""
        try:
            symbol = signal.get("symbol", "PI_XBTUSD")
            side = str(signal.get("side", "buy")).lower()
            quantity = float(signal.get("quantity", 0))
            
            # Get current market price
            current_price = await self._fetch_price(symbol)
            
            order = {
                "id": f"paper_{symbol}_{side}_{int(time.time())}",
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "filled": quantity,
                "avg_fill_price": current_price,
                "price": current_price,
                "status": "filled",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": {
                    "slippage": 0.0,
                    "fill_rate": 1.0,
                    "latency_ms": 0.0,
                },
            }
            return order
        except Exception as e:
            logger.error("Paper executor failed: %s", e)
            return {"status": "rejected", "error": str(e)}

    async def _fetch_price(self, symbol: str) -> float:
        """Fetch current price from Kraken API."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.kraken.com/0/public/Ticker?pair={symbol}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data and symbol in data["result"]:
                            return float(data["result"][symbol]["c"][0])
        except Exception as e:
            logger.warning("Failed to fetch price: %s", e)
        
        return 60000.0  # fallback