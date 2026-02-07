from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.user import User
from app.schemas.portfolio import PortfolioSummary, Position

async def get_portfolio_summary(db: AsyncSession, user_id: int) -> PortfolioSummary:
    """Calculate and return user portfolio summary."""
    positions = await get_user_positions(db, user_id)
    
    equity = 10000.0  # TODO: fetch from database
    cash = 5000.0     # TODO: fetch from database
    total_value = equity + cash
    total_pnl = sum(p.pnl for p in positions)
    
    return PortfolioSummary(
        user_id=user_id,
        equity=equity,
        cash=cash,
        total_value=total_value,
        total_pnl=total_pnl,
        positions=positions,
    )

async def get_user_positions(db: AsyncSession, user_id: int) -> list[Position]:
    """Fetch all open positions for a user."""
    # TODO: query Position model from database
    return []