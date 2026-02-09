from abc import ABC, abstractmethod
from typing import Optional, Any
from datetime import datetime, timezone

from app.models.signals import SignalType, TradingSignal

class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""
    
    def __init__(self, symbol: str, name: str):
        self.symbol = symbol
        self.name = name
    
    @abstractmethod
    async def analyze(self, data: dict[str, Any]) -> Optional[TradingSignal]:
        """Analyze market data and generate a trading signal."""
        pass
    
    def _create_signal(
        self,
        signal_type: SignalType,
        price: float,
        confidence: float,
        reason: str = "",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> TradingSignal:
        """Helper method to create a trading signal with UTC timestamp."""
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")
        if price <= 0:
            raise ValueError("Price must be positive")
        
        return TradingSignal(
            signal=signal_type,
            symbol=self.symbol,
            timestamp=datetime.now(timezone.utc),
            price=price,
            confidence=confidence,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
