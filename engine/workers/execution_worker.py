"""Executes orders from signals."""
import asyncio
import logging

logger = logging.getLogger(__name__)

class ExecutionWorker:
    def __init__(self, execution_engine, signal_queue):
        self.execution_engine = execution_engine
        self.signal_queue = signal_queue
        self.is_running = False
    
    async def start(self):
        self.is_running = True
        while self.is_running:
            try:
                signal = await asyncio.wait_for(self.signal_queue.get(), timeout=1.0)
                result = self.execution_engine.execute(signal)
                logger.info(f"Order executed: {result}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error(f"Execution failed: {e}")
