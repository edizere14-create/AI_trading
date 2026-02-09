import logging
from app.strategies.base import BaseStrategy
from app.models.signals import TradingSignal, SignalType
from typing import Dict, Any, Optional
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
    ) -> None:
        self.symbol = symbol
        self.rsi_period = rsi_period
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
        self.price_history: list[float] = []
    
    def _calculate_rsi(self, prices: list[float]) -> float:
        """Calculate RSI from price data."""
        if len(prices) < self.rsi_period + 1:
            return 50.0
        
        deltas = np.diff(prices[-self.rsi_period - 1:])
        seed = deltas[:self.rsi_period]
        up = seed[seed >= 0].sum() / self.rsi_period
        down = -seed[seed < 0].sum() / self.rsi_period
        
        rs = up / down if down != 0 else 0
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi
    
    async def analyze(self, market_data: Dict[str, Any]) -> Optional[TradingSignal]:
        """Analyze market data and generate trading signals."""
        try:
            prices = market_data.get('prices', [])
            if not prices:
                return None
            
            self.price_history.extend(prices)
            rsi = self._calculate_rsi(self.price_history)
            
            signal_type = None
            if rsi < self.low_threshold:
                signal_type = SignalType.BUY
            elif rsi > self.high_threshold:
                signal_type = SignalType.SELL
            
            if signal_type:
                current_price = prices[-1] if prices else 0
                confidence = abs(rsi - 50) / 50
                timestamp = market_data.get('timestamp')
                if not isinstance(timestamp, datetime):
                    timestamp = datetime.now()
                signal = TradingSignal(
                    symbol=self.symbol,
                    signal=signal_type,
                    timestamp=timestamp,
                    price=current_price,
                    confidence=confidence
                )
                logger.info(f"RSI Strategy generated {signal_type} signal for {self.symbol}: RSI={rsi:.2f}")
                return signal
            
            return None
        except Exception as exc:
            logger.error(f"Error in RSIStrategy.analyze: {exc}")
            return None
