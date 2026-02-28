"""Tracks portfolio state and performance."""
import asyncio
import logging

logger = logging.getLogger(__name__)

class PortfolioWorker:
    def __init__(self, portfolio_manager, interval: int = 60):
        self.portfolio_manager = portfolio_manager
        self.interval = interval
        self.is_running = False
    
    async def start(self):
        self.is_running = True
        while self.is_running:
            try:
                pnl = await self.portfolio_manager.calculate_pnl()
                logger.info(f"Portfolio PnL: {pnl}")
            except Exception as e:
                logger.error(f"Portfolio calc failed: {e}")
            await asyncio.sleep(self.interval)
