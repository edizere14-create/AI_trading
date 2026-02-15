"""
AI Trading Engine Package

Core components:
- futures_adapter      -> Exchange connectivity
- execution_manager    -> Order orchestration
- positions            -> Position state management
- risk                 -> Risk validation and sizing
"""

from .futures_adapter import (
    connect_kraken,
    initialize_exchange,
    test_connection,
)

from .execution_manager import ExecutionManager
from .positions import PositionManager
from .risk import RiskManager


__all__ = [
    "connect_kraken",
    "initialize_exchange",
    "test_connection",
    "ExecutionManager",
    "PositionManager",
    "RiskManager",
]