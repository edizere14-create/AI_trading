from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.user import User
from app.schemas.portfolio import PortfolioSummary, Position, CollateralBalance

async def get_portfolio_summary(db: AsyncSession, user_id: int) -> PortfolioSummary:
    """Calculate and return user portfolio summary."""
    positions = await get_user_positions(db, user_id)
    
    equity = 10000.0  # TODO: fetch from database
    cash = 5000.0     # TODO: fetch from database
    total_value = equity + cash
    total_pnl = sum(p.pnl for p in positions)
    collateral_balances = [
        CollateralBalance(
            asset="USD",
            amount=cash,
            usd_price=1.0,
            haircut_pct=0.0,
            effective_value=cash,
        )
    ]
    effective_collateral_value = sum(c.effective_value for c in collateral_balances)
    
    return PortfolioSummary(
        user_id=user_id,
        equity=equity,
        cash=cash,
        total_value=total_value,
        total_pnl=total_pnl,
        positions=positions,
        collateral_balances=collateral_balances,
        effective_collateral_value=effective_collateral_value,
    )

async def get_user_positions(db: AsyncSession, user_id: int) -> list[Position]:
    """Fetch all open positions for a user."""
    # TODO: query Position model from database
    return []