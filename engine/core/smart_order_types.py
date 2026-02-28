"""Smart order types for advanced execution strategies."""
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class SmartOrderType(str, Enum):
    ICEBERG = "iceberg"
    TWAP = "twap"  # Time-weighted average price
    VWAP = "vwap"  # Volume-weighted average price
    POV = "pov"    # Percentage of volume

@dataclass
class IcebergOrder:
    total_quantity: float
    visible_quantity: float
    interval: int  # seconds

@dataclass
class TWAPOrder:
    total_quantity: float
    duration: int  # seconds
    num_slices: int

@dataclass
class VWAPOrder:
    total_quantity: float
    duration: int  # seconds
