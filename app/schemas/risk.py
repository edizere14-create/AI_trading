from pydantic import BaseModel, Field, model_validator

class TradeCheckRequest(BaseModel):
    symbol: str
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)

class TradeCheckResponse(BaseModel):
    approved: bool
    max_size: float
    reason: str
    max_position_size: float | None = None

    @model_validator(mode="after")
    def _set_compat_max_position_size(self) -> "TradeCheckResponse":
        if self.max_position_size is None:
            self.max_position_size = float(self.max_size)
        return self

class RiskLimits(BaseModel):
    max_risk_per_trade: float = Field(0.02)
    max_daily_drawdown: float = Field(0.05)
    max_position_size: float = Field(0.10)
    stop_loss_pct: float = Field(0.05)

    model_config = {"from_attributes": True}


class RiskEngineConfig(BaseModel):
    max_drawdown_pct: float = Field(0.20, ge=0.0, le=1.0)
    risk_per_trade_pct: float = Field(0.02, ge=0.0, le=1.0)
    max_leverage: float = Field(3.0, gt=0.0)
    trailing_stop_pct: float = Field(0.03, ge=0.0, le=1.0)
    max_portfolio_exposure_pct: float = Field(0.80, ge=0.0)
    max_collateral_bucket_exposure_pct: float = Field(0.60, ge=0.0, le=1.0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "max_drawdown_pct": 0.20,
                "risk_per_trade_pct": 0.02,
                "max_leverage": 3.0,
                "trailing_stop_pct": 0.03,
                "max_portfolio_exposure_pct": 0.80,
                "max_collateral_bucket_exposure_pct": 0.60,
            }
        }
    }


class RiskCheckInput(BaseModel):
    symbol: str
    side: str
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    leverage: float = Field(default=1.0, gt=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "symbol": "PI_XBTUSD",
                "side": "buy",
                "entry_price": 100,
                "stop_price": 98,
                "quantity": 10,
                "leverage": 2.0,
            }
        }
    }


class PositionSnapshotInput(BaseModel):
    symbol: str
    side: str
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    current_price: float = Field(gt=0)
    leverage: float = Field(default=1.0, gt=0)
    trailing_stop_price: float | None = None


class CollateralBalanceInput(BaseModel):
    asset: str
    amount: float = Field(ge=0)
    usd_price: float = Field(gt=0)
    haircut_pct: float = Field(default=0.0, ge=0.0, lt=1.0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "asset": "USDT",
                "amount": 5000.0,
                "usd_price": 1.0,
                "haircut_pct": 0.0,
            }
        }
    }


class RiskCheckApiRequest(BaseModel):
    equity: float | None = 1000.0
    config: RiskEngineConfig | None = None
    trade: RiskCheckInput
    open_positions: list[PositionSnapshotInput] = Field(default_factory=list)
    collateral_balances: list[CollateralBalanceInput] = Field(default_factory=list)
    symbol_collateral_map: dict[str, str] = Field(default_factory=dict)
    collateral_bucket_exposure_limits: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_equity_or_collateral(self) -> "RiskCheckApiRequest":
        if self.equity is not None and float(self.equity) <= 0:
            raise ValueError("equity must be > 0")
        if self.equity is None and not self.collateral_balances:
            raise ValueError("Provide equity or collateral_balances")
        for asset, limit in self.collateral_bucket_exposure_limits.items():
            if float(limit) < 0 or float(limit) > 1:
                raise ValueError(f"collateral_bucket_exposure_limits['{asset}'] must be between 0 and 1")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "equity": 10000,
                "trade": {
                    "symbol": "PI_XBTUSD",
                    "side": "buy",
                    "entry_price": 100,
                    "stop_price": 98,
                    "quantity": 10,
                    "leverage": 2.0,
                },
                "open_positions": [],
                "collateral_balances": [],
                "symbol_collateral_map": {"PI_XBTUSD": "BTC", "PI_ETHUSD": "ETH"},
                "collateral_bucket_exposure_limits": {"BTC": 0.70, "USDT": 0.85},
            }
        }
    }


class RiskCheckOutput(BaseModel):
    approved: bool
    reason: str
    max_position_size: float
    current_drawdown_pct: float
    risk_amount: float
    max_risk_amount: float
    exposure_pct: float
    leverage: float
    trailing_stop_price: float | None = None
    effective_equity: float | None = None
    collateral_assets: list[str] = Field(default_factory=list)
    trade_collateral_asset: str | None = None
    collateral_bucket_exposure_pct: float | None = None
    collateral_bucket_limit_pct: float | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "approved": True,
                "reason": "risk check passed",
                "max_position_size": 120.0,
                "current_drawdown_pct": 0.018,
                "risk_amount": 20.0,
                "max_risk_amount": 200.0,
                "exposure_pct": 0.32,
                "leverage": 2.0,
                "trailing_stop_price": 97.0,
                "effective_equity": 10000.0,
                "collateral_assets": ["USDT", "USD"],
                "trade_collateral_asset": "BTC",
                "collateral_bucket_exposure_pct": 0.41,
                "collateral_bucket_limit_pct": 0.70,
            }
        }
    }
