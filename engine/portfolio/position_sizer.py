"""Position sizing logic based on risk and portfolio allocation."""
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class PositionSizer:
    def __init__(self, account_balance: float, risk_per_trade: float = 0.02):
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
    
    def calculate_size(self, entry_price: float, stop_loss: float) -> float:
        risk_amount = self.account_balance * self.risk_per_trade
        price_risk = abs(entry_price - stop_loss)
        if price_risk == 0:
            return 0
        position_size = risk_amount / price_risk
        return position_size
