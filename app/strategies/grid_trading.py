from typing import Dict, List, Optional
import logging
from decimal import Decimal
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class GridTradingStrategy:
    """
    Enterprise Grid Trading Strategy
    - Divides capital into grids
    - Places buy/sell orders at each level
    - Captures volatility systematically
    """
    
    def __init__(
        self,
        symbol: str,
        grid_levels: int = 20,
        grid_amount: float = 1000,
        profit_percentage: float = 0.5,
        upper_price: float = None,
        lower_price: float = None,
    ):
        self.symbol = symbol
        self.grid_levels = grid_levels
        self.grid_amount = grid_amount
        self.profit_percentage = profit_percentage / 100
        self.upper_price = upper_price
        self.lower_price = lower_price
        self.active_orders: List[Dict] = []
        self.completed_trades = []
        
    def initialize_grid(self, current_price: float) -> List[Dict]:
        """
        Initialize grid with buy/sell orders
        
        Returns:
            List of orders to place
        """
        if not self.upper_price or not self.lower_price:
            self.upper_price = current_price * 1.1
            self.lower_price = current_price * 0.9
        
        price_range = self.upper_price - self.lower_price
        price_step = price_range / self.grid_levels
        
        orders = []
        
        for i in range(self.grid_levels):
            grid_price = self.lower_price + (price_step * i)
            
            if grid_price < current_price:
                # Buy grid
                order = {
                    'side': 'buy',
                    'price': round(grid_price, 2),
                    'amount': self.grid_amount / grid_price,
                    'grid_level': i,
                    'type': 'grid_buy',
                    'created_at': datetime.now(),
                }
            else:
                # Sell grid
                order = {
                    'side': 'sell',
                    'price': round(grid_price, 2),
                    'amount': self.grid_amount / grid_price,
                    'grid_level': i,
                    'type': 'grid_sell',
                    'created_at': datetime.now(),
                }
            
            orders.append(order)
        
        self.active_orders = orders
        logger.info(f"Grid initialized with {len(orders)} levels for {self.symbol}")
        return orders
    
    def on_fill(self, order: Dict, fill_price: float):
        """
        Handle order fill
        - Place offsetting order at profit level
        """
        logger.info(f"Grid order filled: {order['side']} at {fill_price}")
        
        if order['side'] == 'buy':
            # Place sell order above purchase price
            sell_price = fill_price * (1 + self.profit_percentage)
            offset_order = {
                'side': 'sell',
                'price': round(sell_price, 2),
                'amount': order['amount'],
                'grid_level': order['grid_level'],
                'type': 'offset_sell',
                'linked_order': order,
                'created_at': datetime.now(),
            }
        else:
            # Place buy order below sell price
            buy_price = fill_price * (1 - self.profit_percentage)
            offset_order = {
                'side': 'buy',
                'price': round(buy_price, 2),
                'amount': order['amount'],
                'grid_level': order['grid_level'],
                'type': 'offset_buy',
                'linked_order': order,
                'created_at': datetime.now(),
            }
        
        self.active_orders.append(offset_order)
        return offset_order
    
    def adjust_grid(self, current_price: float):
        """
        Dynamically adjust grid based on current price
        """
        if current_price < self.lower_price * 0.95:
            self.lower_price *= 0.95
            self.upper_price *= 0.95
            logger.info(f"Grid adjusted down for {self.symbol}")
            
        elif current_price > self.upper_price * 1.05:
            self.lower_price *= 1.05
            self.upper_price *= 1.05
            logger.info(f"Grid adjusted up for {self.symbol}")
    
    def get_pnl(self) -> Dict:
        """Calculate strategy P&L"""
        total_profit = sum(
            trade.get('pnl', 0) for trade in self.completed_trades
        )
        
        return {
            'total_profit': total_profit,
            'completed_trades': len(self.completed_trades),
            'active_orders': len(self.active_orders),
            'win_rate': self._calculate_win_rate(),
        }
    
    def _calculate_win_rate(self) -> float:
        """Calculate winning trades percentage"""
        if not self.completed_trades:
            return 0.0
        
        wins = sum(1 for t in self.completed_trades if t.get('pnl', 0) > 0)
        return wins / len(self.completed_trades)