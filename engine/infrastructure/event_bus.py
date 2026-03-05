"""Event bus for distributed communication."""
from typing import Callable, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
    
    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
        logger.info(f"Subscribed to {event_type}")
    
    async def publish(self, event_type: str, data: Dict[str, Any]):
        if event_type in self.subscribers:
            for handler in self.subscribers[event_type]:
                await handler(data) if asyncio.iscoroutinefunction(handler) else handler(data)
