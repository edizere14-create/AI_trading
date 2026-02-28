"""Momentum trading strategy."""
import logging
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class MomentumStrategy:
    """Momentum-based trading strategy using rate of change (ROC)."""

    def __init__(
        self,
        symbol: str = "PI_XBTUSD",
        momentum_period: int = 14,
        buy_threshold: float = 1.0,
        sell_threshold: float = -1.0,
    ):
        """
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            momentum_period: Lookback period for momentum calculation
            buy_threshold: Buy signal threshold in percent
            sell_threshold: Sell signal threshold in percent (negative value)
        """
        self.symbol = symbol
        self.momentum_period = momentum_period
        self.buy_threshold = float(buy_threshold)
        self.sell_threshold = float(sell_threshold if sell_threshold < 0 else -abs(sell_threshold))
        self.last_signal = None

    def analyze(self, ohlcv: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Analyze OHLCV data and generate trading signal.

        Args:
            ohlcv: DataFrame with columns ['open', 'high', 'low', 'close', 'volume']

        Returns:
            Signal dict or None
        """
        if len(ohlcv) < self.momentum_period:
            logger.warning(f"Insufficient data: {len(ohlcv)} < {self.momentum_period}")
            return None

        try:
            close = ohlcv["close"]
            momentum = ((close.iloc[-1] - close.iloc[-self.momentum_period]) / close.iloc[-self.momentum_period]) * 100
            logger.info(f"Momentum: {momentum:.2f}%")

            if momentum > self.buy_threshold:
                signal = {
                    "action": "buy",
                    "side": "buy",
                    "momentum": momentum,
                    "price": close.iloc[-1],
                }
                logger.info(f"BUY signal generated - Momentum: {momentum:.2f}%")
            elif momentum < self.sell_threshold:
                signal = {
                    "action": "sell",
                    "side": "sell",
                    "momentum": abs(momentum),
                    "price": close.iloc[-1],
                }
                logger.info(f"SELL signal generated - Momentum: {momentum:.2f}%")
            else:
                signal = None

            self.last_signal = signal
            return signal

        except Exception as e:
            logger.error(f"Momentum analysis failed: {e}", exc_info=True)
            return None

    def generate_signal(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Legacy RSI-based signal method used by existing unit tests."""
        if "rsi" not in data.columns:
            raise ValueError("RSI column is required")
        if data["rsi"].empty:
            raise ValueError("RSI data is empty")

        rsi_value = float(data["rsi"].iloc[-1])
        if rsi_value < 30:
            return {"signal": "buy", "confidence": 0.8}
        if rsi_value > 70:
            return {"signal": "sell", "confidence": 0.8}
        return {"signal": "hold", "confidence": 0.5}
