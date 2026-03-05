"""Manages leverage for margin trading."""
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class LeverageManager:
    def __init__(self, max_leverage: float = 2.0):
        self.max_leverage = max_leverage
        self.current_leverage = 1.0
    
    def set_leverage(self, leverage: float) -> bool:
        if leverage <= self.max_leverage:
            self.current_leverage = leverage
            return True
        return False
    
    def get_leverage(self) -> float:
        return self.current_leverage
