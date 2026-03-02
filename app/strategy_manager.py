"""Strategy manager for loading and managing strategies."""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class StrategySignal:
    signal: SignalType
    confidence: float
    reason: str
    stop_loss: float | None = None
    take_profit: float | None = None


class StrategyManager:
    """Manages strategy lifecycle and execution."""
    
    def __init__(self) -> None:
        self.strategies: Dict[str, Any] = {}
        self._rsi_strategies: Dict[str, dict[str, float]] = {}
        self._load_strategies()
    
    def _load_strategies(self) -> None:
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

    def create_rsi_strategy(self, symbol: str, overbought: float = 70.0, oversold: float = 30.0) -> None:
        """Register a simple RSI threshold strategy for a symbol."""
        symbol_key = str(symbol).strip().upper()
        self._rsi_strategies[symbol_key] = {
            "overbought": float(overbought),
            "oversold": float(oversold),
        }

    async def analyze_all(self, symbol: str, market_data: Dict[str, Any]) -> list[StrategySignal]:
        """Analyze configured strategies and return normalized signal objects."""
        symbol_key = str(symbol).strip().upper()
        cfg = self._rsi_strategies.get(symbol_key)
        if cfg is None:
            return []

        try:
            rsi = float(market_data.get("rsi", 50.0))
        except (TypeError, ValueError):
            rsi = 50.0

        try:
            price = float(market_data.get("price", 0.0))
        except (TypeError, ValueError):
            price = 0.0

        overbought = cfg["overbought"]
        oversold = cfg["oversold"]

        if rsi <= oversold:
            confidence = min(1.0, max(0.0, (oversold - rsi) / max(oversold, 1.0)))
            stop_loss = price * 0.98 if price > 0 else None
            take_profit = price * 1.02 if price > 0 else None
            return [
                StrategySignal(
                    signal=SignalType.BUY,
                    confidence=confidence,
                    reason=f"RSI {rsi:.2f} <= oversold {oversold:.2f}",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
            ]

        if rsi >= overbought:
            confidence = min(1.0, max(0.0, (rsi - overbought) / max(100.0 - overbought, 1.0)))
            stop_loss = price * 1.02 if price > 0 else None
            take_profit = price * 0.98 if price > 0 else None
            return [
                StrategySignal(
                    signal=SignalType.SELL,
                    confidence=confidence,
                    reason=f"RSI {rsi:.2f} >= overbought {overbought:.2f}",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
            ]

        return [
            StrategySignal(
                signal=SignalType.HOLD,
                confidence=0.5,
                reason=f"RSI {rsi:.2f} within neutral range",
                stop_loss=None,
                take_profit=None,
            )
        ]