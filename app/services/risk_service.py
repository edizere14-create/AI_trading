from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.schemas.risk import TradeCheckRequest, TradeCheckResponse, RiskLimits
from app.core.config import settings

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
    default_profile = settings.RISK_PROFILES.get("default", {})
    return RiskLimits(**default_profile)