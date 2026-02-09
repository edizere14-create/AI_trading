from typing import Dict, List, Optional, Any
import logging

from app.strategies.base import BaseStrategy
from app.strategies.momentum import RSIStrategy
from app.models.signals import TradingSignal

logger = logging.getLogger(__name__)


class StrategyManager:
    """Manages multiple trading strategies."""
    
    def __init__(self) -> None:
        self.strategies: Dict[str, BaseStrategy] = {}
    
    def create_rsi_strategy(
        self,
        symbol: str,
        overbought: float = 70.0,
        oversold: float = 30.0
    ) -> None:
        """Create and register an RSI strategy."""
        strategy = RSIStrategy(
            symbol=symbol,
            rsi_period=14,
            low_threshold=oversold,
            high_threshold=overbought
        )
        self.strategies[symbol] = strategy
        logger.info("Created RSI strategy for %s", symbol)
    
    async def analyze_all(
        self,
        symbol: str,
        market_data: Dict[str, Any]
    ) -> List[TradingSignal]:
        """Analyze market data with all registered strategies."""
        signals: List[TradingSignal] = []
        
        for strategy_symbol, strategy in self.strategies.items():
            if strategy_symbol == symbol:
                try:
                    signal = await strategy.analyze(market_data)
                    if signal:
                        signals.append(signal)
                except Exception as exc:
                    logger.error(
                        "Error analyzing with strategy %s: %s",
                        strategy_symbol,
                        exc
                    )
        
        return signals