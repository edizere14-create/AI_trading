"""Monitors and enforces risk limits."""
import asyncio
import logging

logger = logging.getLogger(__name__)

class RiskWorker:
    def __init__(self, risk_manager, interval: int = 30):
        self.risk_manager = risk_manager
        self.interval = interval
        self.is_running = False
    
    async def start(self):
        self.is_running = True
        while self.is_running:
            try:
                await self.risk_manager.check_limits()
                logger.info("Risk check passed")
            except Exception as e:
                logger.error(f"Risk check failed: {e}")
            await asyncio.sleep(self.interval)
