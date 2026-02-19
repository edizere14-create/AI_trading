from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DCAStrategy:
    """
    Dollar-Cost Averaging Strategy
    - Allocates fixed amount at regular intervals
    - Reduces timing risk
    - Perfect for long-term accumulation
    """
    
    def __init__(
        self,
        symbol: str,
        investment_amount: float,
        interval_days: int = 1,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ):
        self.symbol = symbol
        self.investment_amount = investment_amount
        self.interval_days = interval_days
        self.min_price = min_price
        self.max_price = max_price
        self.next_buy_date = datetime.now()
        self.purchases: List[Dict] = []
        self.total_invested = 0.0
        self.total_quantity = 0.0
        
    def should_buy(self, current_price: float) -> bool:
        """Determine if DCA buy should execute"""
        # Check time interval
        if datetime.now() < self.next_buy_date:
            return False
        
        # Check price constraints
        if self.min_price and current_price < self.min_price:
            logger.info(f"Price {current_price} below min {self.min_price}, skipping DCA")
            return False
        
        if self.max_price and current_price > self.max_price:
            logger.info(f"Price {current_price} above max {self.max_price}, skipping DCA")
            return False
        
        return True
    
    def execute_buy(self, current_price: float) -> Dict:
        """Execute DCA buy order"""
        quantity = self.investment_amount / current_price
        
        order = {
            'symbol': self.symbol,
            'side': 'buy',
            'price': current_price,
            'quantity': quantity,
            'amount': self.investment_amount,
            'timestamp': datetime.now(),
            'average_cost': self.get_average_cost(),
        }
        
        self.purchases.append(order)
        self.total_invested += self.investment_amount
        self.total_quantity += quantity
        self.next_buy_date = datetime.now() + timedelta(days=self.interval_days)
        
        logger.info(
            f"DCA buy executed: {quantity:.4f} {self.symbol} @ {current_price} "
            f"| Avg cost: {self.get_average_cost():.2f}"
        )
        
        return order
    
    def get_average_cost(self) -> float:
        """Calculate average cost per unit"""
        if self.total_quantity == 0:
            return 0.0
        return self.total_invested / self.total_quantity
    
    def get_statistics(self, current_price: float) -> Dict:
        """Get DCA statistics"""
        current_value = self.total_quantity * current_price
        unrealized_pnl = current_value - self.total_invested
        return_pct = (unrealized_pnl / self.total_invested * 100) if self.total_invested > 0 else 0
        
        return {
            'total_invested': self.total_invested,
            'total_quantity': self.total_quantity,
            'average_cost': self.get_average_cost(),
            'current_price': current_price,
            'current_value': current_value,
            'unrealized_pnl': unrealized_pnl,
            'return_percent': return_pct,
            'purchases_count': len(self.purchases),
            'next_buy_date': self.next_buy_date.isoformat(),
        }
    
    def get_purchases(self) -> List[Dict]:
        """Return all purchases"""
        return self.purchases