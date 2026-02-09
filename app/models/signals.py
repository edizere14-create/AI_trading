from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator

class SignalType(str, Enum):
    """Trading signal types - str, Enum for Pydantic compatibility."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    STRONG_BUY = "strong_buy"
    STRONG_SELL = "strong_sell"

@dataclass
class TradingSignal:
    """Core trading signal with validation."""
    signal: SignalType
    symbol: str
    timestamp: datetime
    price: float
    confidence: float
    quantity: Optional[float] = None
    reason: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    def __post_init__(self) -> None:
        """Validate confidence is between 0 and 1."""
        if not 0 <= self.confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")
        if self.price <= 0:
            raise ValueError("Price must be positive")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("Quantity must be positive")


class SignalMetadata(BaseModel):
    """Metadata for advanced trading signals."""
    model_config = ConfigDict(protected_namespaces=())
    
    indicators: Dict[str, float]
    model_version: str
    execution_time_ms: float

class AdvancedTradingSignal(BaseModel):
    """Advanced trading signal with Pydantic validation for API responses."""
    signal: SignalType
    symbol: str
    timestamp: datetime
    price: float = Field(gt=0, description="Current price")
    confidence: float = Field(ge=0, le=1, description="Signal confidence")
    quantity: Optional[float] = Field(None, gt=0)
    stop_loss: Optional[float] = Field(None, gt=0)
    take_profit: Optional[float] = Field(None, gt=0)
    risk_reward_ratio: Optional[float] = None
    metadata: Optional[SignalMetadata] = None
    tags: List[str] = []
    
    @field_validator('confidence')
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Ensure confidence is between 0 and 1."""
        if not 0 <= v <= 1:
            raise ValueError('Confidence must be between 0 and 1')
        return v
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/API."""
        return self.model_dump(exclude_none=True)