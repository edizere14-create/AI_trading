from typing import Dict, List, Optional
from decimal import Decimal
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ArbitrageStrategy:
    """
    Arbitrage Strategy
    - Exploits price discrepancies across exchanges
    - Simultaneous buy/sell for risk-free profit
    - Requires low latency + liquidity
    """
    
    def __init__(
        self,
        symbol: str,
        min_spread_percent: float = 0.5,
        max_position_size: float = 10000,
    ):
        self.symbol = symbol
        self.min_spread_percent = min_spread_percent / 100
        self.max_position_size = max_position_size
        self.opportunities: List[Dict] = []
        self.executed_trades: List[Dict] = []
        
    def scan_arbitrage(
        self,
        exchange1_price: float,
        exchange2_price: float,
        exchange1: str,
        exchange2: str,
    ) -> Optional[Dict]:
        """
        Scan for arbitrage opportunities
        
        Returns:
            Arbitrage opportunity dict or None
        """
        if exchange1_price <= 0 or exchange2_price <= 0:
            return None
        
        # Calculate spread
        if exchange1_price < exchange2_price:
            spread = (exchange2_price - exchange1_price) / exchange1_price
            buy_exchange = exchange1
            sell_exchange = exchange2
            buy_price = exchange1_price
            sell_price = exchange2_price
        else:
            spread = (exchange1_price - exchange2_price) / exchange2_price
            buy_exchange = exchange2
            sell_exchange = exchange1
            buy_price = exchange2_price
            sell_price = exchange1_price
        
        # Check if spread exceeds minimum
        if spread < self.min_spread_percent:
            return None
        
        opportunity = {
            'symbol': self.symbol,
            'buy_exchange': buy_exchange,
            'sell_exchange': sell_exchange,
            'buy_price': buy_price,
            'sell_price': sell_price,
            'spread_percent': spread * 100,
            'timestamp': datetime.now(),
            'status': 'detected',
        }
        
        self.opportunities.append(opportunity)
        logger.info(
            f"Arbitrage opportunity: Buy {buy_exchange} @ {buy_price}, "
            f"Sell {sell_exchange} @ {sell_price} "
            f"({spread * 100:.2f}% spread)"
        )
        
        return opportunity
    
    def execute_arbitrage(
        self,
        opportunity: Dict,
        amount: float,
    ) -> Dict:
        """
        Execute arbitrage trade
        
        Args:
            opportunity: Detected opportunity dict
            amount: Trade amount in base currency
        """
        # Check position size
        if amount > self.max_position_size:
            logger.warning(f"Amount {amount} exceeds max position {self.max_position_size}")
            amount = self.max_position_size
        
        buy_qty = amount / opportunity['buy_price']
        sell_qty = amount / opportunity['sell_price']
        
        trade = {
            'symbol': self.symbol,
            'buy_exchange': opportunity['buy_exchange'],
            'sell_exchange': opportunity['sell_exchange'],
            'buy_order': {
                'exchange': opportunity['buy_exchange'],
                'price': opportunity['buy_price'],
                'amount': amount,
                'quantity': buy_qty,
                'status': 'pending',
            },
            'sell_order': {
                'exchange': opportunity['sell_exchange'],
                'price': opportunity['sell_price'],
                'amount': amount,
                'quantity': sell_qty,
                'status': 'pending',
            },
            'gross_profit': (opportunity['sell_price'] - opportunity['buy_price']) * buy_qty,
            'spread_percent': opportunity['spread_percent'],
            'created_at': datetime.now(),
        }
        
        self.executed_trades.append(trade)
        logger.info(
            f"Arbitrage executed: {buy_qty:.4f} {self.symbol} "
            f"| Gross profit: {trade['gross_profit']:.2f}"
        )
        
        return trade
    
    def calculate_net_profit(
        self,
        gross_profit: float,
        buy_fee_percent: float = 0.1,
        sell_fee_percent: float = 0.1,
        transfer_fee: float = 0.0,
    ) -> Dict:
        """
        Calculate net profit after fees
        
        Typical Kraken fees: 0.16% - 0.26%
        """
        buy_fee = gross_profit * (buy_fee_percent / 100)
        sell_fee = gross_profit * (sell_fee_percent / 100)
        total_fees = buy_fee + sell_fee + transfer_fee
        net_profit = gross_profit - total_fees
        
        return {
            'gross_profit': gross_profit,
            'buy_fee': buy_fee,
            'sell_fee': sell_fee,
            'transfer_fee': transfer_fee,
            'total_fees': total_fees,
            'net_profit': net_profit,
            'roi_percent': (net_profit / gross_profit * 100) if gross_profit > 0 else 0,
        }
    
    def get_statistics(self) -> Dict:
        """Get strategy statistics"""
        if not self.executed_trades:
            return {
                'total_trades': 0,
                'total_gross_profit': 0,
                'win_rate': 0,
                'avg_spread': 0,
            }
        
        total_profit = sum(t['gross_profit'] for t in self.executed_trades)
        wins = sum(1 for t in self.executed_trades if t['gross_profit'] > 0)
        avg_spread = sum(t['spread_percent'] for t in self.executed_trades) / len(self.executed_trades)
        
        return {
            'total_trades': len(self.executed_trades),
            'total_gross_profit': total_profit,
            'win_rate': (wins / len(self.executed_trades) * 100) if self.executed_trades else 0,
            'avg_spread': avg_spread,
            'opportunities_detected': len(self.opportunities),
        }