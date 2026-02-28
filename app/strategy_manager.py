"""Strategy manager for loading and managing strategies."""
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class StrategyManager:
    """Manages strategy lifecycle and execution."""
    
    def __init__(self):
        self.strategies: Dict[str, Any] = {}
        self._load_strategies()
    
    def _load_strategies(self):
        """Load available strategies."""
        strategy_cls = None
        try:
            from app.strategies.momentum import MomentumStrategy as _MomentumStrategy
            strategy_cls = _MomentumStrategy
        except Exception:
            try:
                from app.strategies.momentum import RSIStrategy as _RSIStrategy
                strategy_cls = _RSIStrategy
            except Exception as exc:
                logger.warning("Momentum strategy unavailable: %s", exc)

        if strategy_cls is None:
            self.strategies = {}
            return

        try:
            strategy = strategy_cls(
                symbol="BTC/USD",
                momentum_period=5,
                buy_threshold=0.2,
                sell_threshold=-0.2,
            )
        except TypeError:
            strategy = strategy_cls(
                symbol="BTC/USD",
                rsi_period=14,
                low_threshold=30.0,
                high_threshold=70.0,
            )

        self.strategies = {"momentum": strategy}
        logger.info(f"Loaded {len(self.strategies)} strategies")
    
    def get_strategy(self, name: str) -> Optional[Any]:
        """Get strategy by name."""
        return self.strategies.get(name)
    
    def list_strategies(self) -> Dict[str, Any]:
        """List all available strategies."""
        return self.strategies