"""Task queue for async job processing."""
from typing import Callable, Any
import asyncio
import logging

logger = logging.getLogger(__name__)

class TaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
    
    async def enqueue(self, task: Callable, *args, **kwargs):
        await self.queue.put((task, args, kwargs))
        logger.info(f"Task enqueued: {task.__name__}")
    
    async def process(self):
        while True:
            task, args, kwargs = await self.queue.get()
            try:
                await task(*args, **kwargs) if asyncio.iscoroutinefunction(task) else task(*args, **kwargs)
            except Exception as e:
                logger.error(f"Task failed: {e}")
            finally:
                self.queue.task_done()
