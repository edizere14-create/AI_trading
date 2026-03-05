from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from dataclasses import dataclass
from typing import Iterable, Optional

from app.schemas.risk import (
    TradeCheckRequest,
    TradeCheckResponse,
    RiskLimits,
    RiskEngineConfig,
    RiskCheckInput,
    RiskCheckOutput,
)
from app.core.config import settings


@dataclass
class PositionSnapshot:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    leverage: float = 1.0
    trailing_stop_price: Optional[float] = None

    @property
    def notional(self) -> float:
        return abs(float(self.quantity) * float(self.current_price))


class RiskEngine:
    def __init__(
        self,
        starting_equity: float,
        config: RiskEngineConfig | None = None,
    ) -> None:
        self.starting_equity = float(starting_equity)
        self.equity = float(starting_equity)
        self.peak_equity = float(starting_equity)
        self.config = config or RiskEngineConfig(
            max_drawdown_pct=0.20,
            risk_per_trade_pct=0.02,
            max_leverage=3.0,
            trailing_stop_pct=0.03,
            max_portfolio_exposure_pct=0.80,
        )

    def set_equity(self, equity: float) -> None:
        self.equity = float(equity)
        self.peak_equity = max(self.peak_equity, self.equity)

    def current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    def max_position_size(self, entry_price: float, stop_price: float) -> float:
        price_risk = abs(float(entry_price) - float(stop_price))
        if price_risk <= 0:
            return 0.0
        max_risk_amount = self.equity * self.config.risk_per_trade_pct
        return max(0.0, max_risk_amount / price_risk)

    def portfolio_exposure_pct(self, positions: Iterable[PositionSnapshot]) -> float:
        if self.equity <= 0:
            return 0.0
        total_notional = sum(p.notional for p in positions)
        return max(0.0, total_notional / self.equity)

    def initial_trailing_stop(self, side: str, entry_price: float) -> float:
        entry_price = float(entry_price)
        if side.lower() == "sell":
            return entry_price * (1.0 + self.config.trailing_stop_pct)
        return entry_price * (1.0 - self.config.trailing_stop_pct)

    def update_trailing_stop(self, position: PositionSnapshot, market_price: float) -> float:
        market_price = float(market_price)
        current_stop = position.trailing_stop_price
        if current_stop is None:
            current_stop = self.initial_trailing_stop(position.side, position.entry_price)

        if position.side.lower() == "sell":
            candidate = market_price * (1.0 + self.config.trailing_stop_pct)
            new_stop = min(float(current_stop), candidate)
        else:
            candidate = market_price * (1.0 - self.config.trailing_stop_pct)
            new_stop = max(float(current_stop), candidate)

        position.trailing_stop_price = float(new_stop)
        position.current_price = market_price
        return float(new_stop)

    def check_trade(
        self,
        trade: RiskCheckInput,
        open_positions: list[PositionSnapshot] | None = None,
    ) -> RiskCheckOutput:
        open_positions = open_positions or []

        dd = self.current_drawdown_pct()
        if dd >= self.config.max_drawdown_pct:
            return RiskCheckOutput(
                approved=False,
                reason="max drawdown exceeded",
                max_position_size=0.0,
                current_drawdown_pct=dd,
                risk_amount=0.0,
                max_risk_amount=self.equity * self.config.risk_per_trade_pct,
                exposure_pct=self.portfolio_exposure_pct(open_positions),
                leverage=trade.leverage,
            )

        if trade.leverage > self.config.max_leverage:
            return RiskCheckOutput(
                approved=False,
                reason=f"leverage exceeds cap ({self.config.max_leverage})",
                max_position_size=0.0,
                current_drawdown_pct=dd,
                risk_amount=0.0,
                max_risk_amount=self.equity * self.config.risk_per_trade_pct,
                exposure_pct=self.portfolio_exposure_pct(open_positions),
                leverage=trade.leverage,
            )

        max_size = self.max_position_size(trade.entry_price, trade.stop_price)
        risk_amount = abs(trade.entry_price - trade.stop_price) * trade.quantity
        max_risk_amount = self.equity * self.config.risk_per_trade_pct

        if trade.quantity > max_size:
            return RiskCheckOutput(
                approved=False,
                reason="position size exceeds risk-per-trade limit",
                max_position_size=max_size,
                current_drawdown_pct=dd,
                risk_amount=risk_amount,
                max_risk_amount=max_risk_amount,
                exposure_pct=self.portfolio_exposure_pct(open_positions),
                leverage=trade.leverage,
            )

        new_position = PositionSnapshot(
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            entry_price=trade.entry_price,
            current_price=trade.entry_price,
            leverage=trade.leverage,
        )
        exposure_pct = self.portfolio_exposure_pct([*open_positions, new_position])
        if exposure_pct > self.config.max_portfolio_exposure_pct:
            return RiskCheckOutput(
                approved=False,
                reason="portfolio exposure limit exceeded",
                max_position_size=max_size,
                current_drawdown_pct=dd,
                risk_amount=risk_amount,
                max_risk_amount=max_risk_amount,
                exposure_pct=exposure_pct,
                leverage=trade.leverage,
            )

        trailing_stop = self.initial_trailing_stop(trade.side, trade.entry_price)
        return RiskCheckOutput(
            approved=True,
            reason="risk check passed",
            max_position_size=max_size,
            current_drawdown_pct=dd,
            risk_amount=risk_amount,
            max_risk_amount=max_risk_amount,
            exposure_pct=exposure_pct,
            leverage=trade.leverage,
            trailing_stop_price=trailing_stop,
        )

async def check_trade_risk(
    db: AsyncSession,
    user_id: int,
    trade_data: TradeCheckRequest,
) -> TradeCheckResponse:
    """Validate trade against user risk limits."""
    limits = await get_user_risk_limits(db, user_id)
    
    # Calculate position size
    risk_amount = trade_data.entry_price * trade_data.quantity * limits.max_risk_per_trade
    max_size = trade_data.entry_price / limits.max_risk_per_trade
    
    if trade_data.quantity > max_size:
        return TradeCheckResponse(
            approved=False,
            max_size=max_size,
            reason=f"Position size exceeds limit. Max: {max_size}",
        )
    
    return TradeCheckResponse(
        approved=True,
        max_size=max_size,
        reason="Within limits",
    )

async def get_user_risk_limits(db: AsyncSession, user_id: int) -> RiskLimits:
    """Fetch user's risk preferences from config or database."""
    # TODO: fetch from database if user has custom limits
    default_profile = settings.RISK_PROFILES.get("medium", settings.RISK_PROFILES.get("default", {}))
    return RiskLimits(**default_profile)