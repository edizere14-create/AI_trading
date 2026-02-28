"""
Slippage adjustment model for realistic order execution pricing.
"""
import logging
from typing import Dict
import numpy as np

logger = logging.getLogger(__name__)


class SlippageModel:
    """
    Estimates and adjusts prices for slippage based on
    order size, volatility, and market depth.
    """
    
    def __init__(self, base_slippage: float = 0.001):
        """
        Args:
            base_slippage: Base slippage % (0.1% default)
        """
        self.base_slippage = base_slippage
        self.slippage_cache: Dict[str, float] = {}
    
    def adjust(
        self,
        symbol: str,
        side: str,
        size: float,
        volatility: float = 0.02,
        market_depth: float = 1.0,
    ) -> float:
        """
        Calculate slippage-adjusted price for an order.
        
        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            size: Order size (in quote currency units)
            volatility: Annualized volatility (0.02 = 2%)
            market_depth: Market depth multiplier (1.0 = normal)
        
        Returns:
            Slippage-adjusted price multiplier (e.g., 0.9995 for -5 bps)
        """
        try:
            # Size-based slippage (larger orders = more slippage)
            size_adjustment = 1.0 + (size / 10000.0) * 0.01  # Scale: 1% per 10k size
            
            # Volatility-based slippage (higher vol = more slippage)
            vol_adjustment = 1.0 + volatility * 0.5  # 50% of volatility
            
            # Market depth adjustment
            depth_adjustment = market_depth
            
            # Combined slippage
            total_slippage = self.base_slippage * size_adjustment * vol_adjustment * depth_adjustment
            
            # Apply directional adjustment
            if side == "buy":
                price_multiplier = 1.0 + total_slippage  # Pay more when buying
            else:  # sell
                price_multiplier = 1.0 - total_slippage  # Receive less when selling
            
            logger.info(
                f"Slippage {symbol} {side}: {total_slippage:.4f} "
                f"(size_adj={size_adjustment:.4f}, vol_adj={vol_adjustment:.4f})"
            )
            
            self.slippage_cache[symbol] = total_slippage
            return price_multiplier
            
        except Exception as e:
            logger.error(f"Slippage calculation failed: {e}")
            return 1.0  # No adjustment on error
    
    def get_cached_slippage(self, symbol: str) -> float:
        """Get last calculated slippage for symbol."""
        return self.slippage_cache.get(symbol, self.base_slippage)