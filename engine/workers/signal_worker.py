"""Analyzes signals from multiple strategies."""
import asyncio
import logging

logger = logging.getLogger(__name__)

class SignalWorker:
    def __init__(self, strategies: list, interval: int = 60):
        self.strategies = strategies
        self.interval = interval
        self.is_running = False
        self.signals = []
    
    async def start(self):
        self.is_running = True
        while self.is_running:
            for strategy in self.strategies:
                signal = await strategy.generate_signal()
                if signal:
                    self.signals.append(signal)
                    logger.info(f"Signal: {signal}")
            await asyncio.sleep(self.interval)
