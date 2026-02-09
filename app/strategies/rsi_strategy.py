from typing import Optional
import logging

from app.strategies.base import BaseStrategy
from app.models.signals import SignalType, TradingSignal

logger = logging.getLogger(__name__)

class RSIStrategy(BaseStrategy):
    """RSI (Relative Strength Index) based trading strategy."""
    
    def __init__(self, symbol: str, overbought: float = 70, oversold: float = 30):
        super().__init__(symbol, "RSI Strategy")
        self.overbought = overbought
        self.oversold = oversold
        
        # Validate thresholds
        if not 0 <= oversold < overbought <= 100:
            raise ValueError("Invalid RSI thresholds")
    
    async def analyze(self, data: dict[str, float]) -> Optional[TradingSignal]:
        """Analyze RSI indicator and generate signals."""
        rsi = data.get("rsi")
        price = data.get("price")
        
        # Validate inputs
        if rsi is None or price is None:
            logger.debug("Missing RSI or price data for %s", self.symbol)
            return None
        
        if not 0 <= rsi <= 100:
            logger.warning("Invalid RSI value: %s for %s", rsi, self.symbol)
            return None
        
        if price <= 0:
            logger.warning("Invalid price: %s for %s", price, self.symbol)
            return None
        
        # Calculate stop loss and take profit (2% rule)
        stop_loss_pct = 0.02
        take_profit_pct = 0.04
        
        # Strong buy signal (oversold)
        if rsi < self.oversold:
            stop_loss = price * (1 - stop_loss_pct)
            take_profit = price * (1 + take_profit_pct)
            return self._create_signal(
                signal_type=SignalType.STRONG_BUY,
                price=price,
                confidence=0.85,
                reason=f"RSI {rsi:.2f} below oversold level {self.oversold}",
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        
        # Buy signal
        elif rsi < self.oversold + 10:
            stop_loss = price * (1 - stop_loss_pct)
            take_profit = price * (1 + take_profit_pct)
            return self._create_signal(
                signal_type=SignalType.BUY,
                price=price,
                confidence=0.70,
                reason=f"RSI {rsi:.2f} approaching oversold",
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        
        # Strong sell signal (overbought)
        elif rsi > self.overbought:
            stop_loss = price * (1 + stop_loss_pct)
            take_profit = price * (1 - take_profit_pct)
            return self._create_signal(
                signal_type=SignalType.STRONG_SELL,
                price=price,
                confidence=0.85,
                reason=f"RSI {rsi:.2f} above overbought level {self.overbought}",
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        
        # Sell signal
        elif rsi > self.overbought - 10:
            stop_loss = price * (1 + stop_loss_pct)
            take_profit = price * (1 - take_profit_pct)
            return self._create_signal(
                signal_type=SignalType.SELL,
                price=price,
                confidence=0.70,
                reason=f"RSI {rsi:.2f} approaching overbought",
                stop_loss=stop_loss,
                take_profit=take_profit
            )
        
        # Hold (neutral zone)
        else:
            return self._create_signal(
                signal_type=SignalType.HOLD,
                price=price,
                confidence=0.60,
                reason=f"RSI {rsi:.2f} in neutral zone"
            )