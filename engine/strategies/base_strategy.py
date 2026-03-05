"""Base class for all strategies."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseStrategy(ABC):
    def __init__(self, symbol: str):
        self.symbol = symbol
    
    @abstractmethod
    async def analyze(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pass
    
    @abstractmethod
    async def generate_signal(self) -> Optional[Dict[str, Any]]:
        pass
