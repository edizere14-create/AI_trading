"""Redis client for caching and pub/sub."""
import redis
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)

class RedisClient:
    def __init__(self, host: str = "localhost", port: int = 6379):
        self.client = redis.Redis(host=host, port=port, decode_responses=True)
    
    def set(self, key: str, value: Any, ttl: int = None):
        self.client.set(key, value, ex=ttl)
    
    def get(self, key: str) -> Optional[str]:
        return self.client.get(key)
    
    def publish(self, channel: str, message: str):
        self.client.publish(channel, message)
    
    def subscribe(self, channels: list):
        return self.client.pubsub().subscribe(channels)
