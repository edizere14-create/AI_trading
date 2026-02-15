import logging
from app.strategies.base import BaseStrategy
from app.models.signals import TradingSignal, SignalType
from typing import Any, Dict, List, Optional
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)

class RSIStrategy(BaseStrategy):
    """RSI-based trading strategy."""
    
    def __init__(
        self,
        symbol: str,
        rsi_period: int = 14,
        low_threshold: float = 30.0,
        high_threshold: float = 70.0
    ):
        super().__init__(symbol)
        self.rsi_period = rsi_period
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
    
    async def analyze(self, market_data: Dict[str, Any]) -> Optional[TradingSignal]:
        """Analyze market data and generate trading signal."""
        rsi_value = market_data.get('rsi')
        current_price = market_data.get('price')
        
        if rsi_value is None or current_price is None:
            return None
        
        if rsi_value < self.low_threshold:
            return TradingSignal(
                signal=SignalType.BUY,
                symbol=self.symbol,
                timestamp=datetime.now(),
                price=current_price,
                confidence=1.0,
                reason=f'RSI oversold: {rsi_value}'
            )
        elif rsi_value > self.high_threshold:
            return TradingSignal(
                signal=SignalType.SELL,
                symbol=self.symbol,
                timestamp=datetime.now(),
                price=current_price,
                confidence=1.0,
                reason=f'RSI overbought: {rsi_value}'
            )
        
        return None
    
    def generate_signals(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate signals for backtesting."""
        if not data:
            return {}
        
        latest_data = data[-1]
        rsi_value = latest_data.get('rsi', 50.0)
        
        if rsi_value < self.low_threshold:
            return {'action': 'buy', 'rsi': rsi_value}
        elif rsi_value > self.high_threshold:
            return {'action': 'sell', 'rsi': rsi_value}
        
        return {'action': 'hold', 'rsi': rsi_value}
