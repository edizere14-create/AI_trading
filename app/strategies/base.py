from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from app.models.signals import TradingSignal


class BaseStrategy(ABC):
    """Base class for all trading strategies."""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
    
    @abstractmethod
    async def analyze(self, market_data: Dict[str, Any]) -> Optional[TradingSignal]:
        """Analyze market data and generate trading signal.
        
        Args:
            market_data: Dictionary containing market data (price, volume, etc.)
            
        Returns:
            TradingSignal if conditions are met, None otherwise
        """
        pass
    
    @abstractmethod
    def generate_signals(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate trading signals based on historical data.
        
        Args:
            data: List of historical data dictionaries
            
        Returns:
            Dictionary containing signal information (e.g., {'action': 'buy'/'sell'})
        """
        pass
