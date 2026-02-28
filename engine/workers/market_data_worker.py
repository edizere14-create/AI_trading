"""Fetches and distributes market data."""
import asyncio
import logging

logger = logging.getLogger(__name__)

class MarketDataWorker:
    def __init__(self, data_service, symbols: list, interval: int = 60):
        self.data_service = data_service
        self.symbols = symbols
        self.interval = interval
        self.is_running = False
    
    async def start(self):
        self.is_running = True
        while self.is_running:
            for symbol in self.symbols:
                try:
                    data = await self.data_service.get_ohlcv(symbol, "1h")
                    logger.info(f"Market data fetched: {symbol}")
                except Exception as e:
                    logger.error(f"Fetch failed: {e}")
            await asyncio.sleep(self.interval)
