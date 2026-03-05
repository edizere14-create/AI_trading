"""Allocates capital across strategies."""
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class PortfolioAllocator:
    def __init__(self, total_capital: float):
        self.total_capital = total_capital
        self.allocations: Dict[str, float] = {}
    
    def allocate(self, strategy_id: str, percentage: float) -> float:
        if sum(self.allocations.values()) + percentage > 1.0:
            return 0
        amount = self.total_capital * percentage
        self.allocations[strategy_id] = percentage
        return amount
    
    def get_allocation(self, strategy_id: str) -> float:
        return self.allocations.get(strategy_id, 0) * self.total_capital
