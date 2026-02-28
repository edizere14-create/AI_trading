"""
Order routing logic for different order types and market conditions.
"""
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class OrderRouter:
    """
    Routes orders to exchange based on market conditions,
    order type, and liquidity.
    """
    
    def __init__(self, exchange):
        """
        Args:
            exchange: CCXT exchange instance
        """
        self.exchange = exchange
    
    def route_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
    ) -> Dict[str, Any]:
        """
        Route a market order (execute immediately at best price).
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            side: "buy" or "sell"
            amount: Order size
        
        Returns:
            Order result from exchange
        """
        try:
            logger.info(f"Routing market order: {side} {amount} {symbol}")
            order = self.exchange.create_market_order(symbol, side, amount)
            logger.info(f"Market order placed: {order['id']}")
            return order
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            raise
    
    def route_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
    ) -> Dict[str, Any]:
        """
        Route a limit order (execute at specific price or better).
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            side: "buy" or "sell"
            amount: Order size
            price: Limit price
        
        Returns:
            Order result from exchange
        """
        try:
            logger.info(f"Routing limit order: {side} {amount} {symbol} @ {price}")
            order = self.exchange.create_limit_order(symbol, side, amount, price)
            logger.info(f"Limit order placed: {order['id']}")
            return order
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            raise
    
    def route_stop_loss(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
    ) -> Dict[str, Any]:
        """
        Route a stop-loss order.
        
        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            amount: Order size
            stop_price: Price to trigger order
        
        Returns:
            Order result from exchange
        """
        try:
            logger.info(f"Routing stop order: {side} {amount} {symbol} stop @ {stop_price}")
            order = self.exchange.create_order(
                symbol,
                "stop_loss_limit",
                side,
                amount,
                stop_price=stop_price,
            )
            logger.info(f"Stop order placed: {order['id']}")
            return order
        except Exception as e:
            logger.error(f"Stop order failed: {e}")
            raise
    
    def check_liquidity(self, symbol: str, amount: float) -> bool:
        """
        Check if order size has sufficient liquidity.
        
        Args:
            symbol: Trading pair
            amount: Order size
        
        Returns:
            True if liquid, False otherwise
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            bid_volume = ticker.get("bidVolume", 0)
            ask_volume = ticker.get("askVolume", 0)
            available_liquidity = max(bid_volume, ask_volume)
            
            is_liquid = amount <= available_liquidity * 0.1  # 10% of available
            logger.info(f"Liquidity check {symbol}: {is_liquid} (size={amount}, available={available_liquidity})")
            return is_liquid
        except Exception as e:
            logger.error(f"Liquidity check failed: {e}")
            return False