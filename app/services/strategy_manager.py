from typing import Dict, Optional, List
import logging

from app.strategies.base import BaseStrategy
from app.strategies.rsi_strategy import RSIStrategy
from app.models.signals import TradingSignal

logger = logging.getLogger(__name__)

class StrategyManager:
    """Manages multiple trading strategies with proper error handling."""
    
    def __init__(self) -> None:
        self.strategies: Dict[str, BaseStrategy] = {}
    
    def register_strategy(self, symbol: str, strategy: BaseStrategy) -> None:
        """Register a strategy for a symbol."""
        key = f"{symbol}_{strategy.name}"
        self.strategies[key] = strategy
        logger.info("Registered strategy: %s for %s", strategy.name, symbol)
    
    async def analyze_all(self, symbol: str, data: dict) -> List[TradingSignal]:
        """Analyze with all strategies for a symbol."""
        signals = []
        for key, strategy in self.strategies.items():
            if symbol in key:
                try:
                    signal = await strategy.analyze(data)
                    if signal:
                        signals.append(signal)
                        logger.debug(
                            "Signal generated: %s for %s by %s",
                            signal.signal.value,
                            symbol,
                            strategy.name
                        )
                except Exception as exc:
                    logger.error(
                        "Error analyzing %s with %s: %s",
                        symbol,
                        strategy.name,
                        exc
                    )
        return signals
    
    async def analyze_single(
        self,
        symbol: str,
        strategy_name: str,
        data: dict
    ) -> Optional[TradingSignal]:
        """Analyze with a specific strategy."""
        key = f"{symbol}_{strategy_name}"
        strategy = self.strategies.get(key)
        
        if not strategy:
            logger.warning("Strategy not found: %s for %s", strategy_name, symbol)
            return None
        
        try:
            return await strategy.analyze(data)
        except Exception as exc:
            logger.error("Error analyzing %s with %s: %s", symbol, strategy_name, exc)
            return None
    
    def create_rsi_strategy(
        self,
        symbol: str,
        overbought: float = 70,
        oversold: float = 30
    ) -> None:
        """Create and register an RSI strategy."""
        try:
            strategy = RSIStrategy(symbol, overbought, oversold)
            self.register_strategy(symbol, strategy)
        except Exception as exc:
            logger.error("Failed to create RSI strategy for %s: %s", symbol, exc)
    
    def remove_strategy(self, symbol: str, strategy_name: str) -> bool:
        """Remove a strategy."""
        key = f"{symbol}_{strategy_name}"
        if key in self.strategies:
            del self.strategies[key]
            logger.info("Removed strategy: %s for %s", strategy_name, symbol)
            return True
        return False
    
    def get_active_strategies(self, symbol: str) -> List[str]:
        """Get list of active strategies for a symbol."""
        return [
            strategy.name
            for key, strategy in self.strategies.items()
            if symbol in key
        ]