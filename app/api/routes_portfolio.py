from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.portfolio import PortfolioSummary, Position
from app.services.portfolio_service import get_portfolio_summary, get_user_positions
from app.utils.dependencies import get_db_dep, get_current_user
from app.db.models.user import User

router = APIRouter()

@router.get("/", response_model=PortfolioSummary)
async def get_portfolio(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> PortfolioSummary:
    """Get user portfolio summary (equity, positions, PnL)."""
    return await get_portfolio_summary(db, user.id)

@router.get("/positions", response_model=list[Position])
async def get_positions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> list[Position]:
    """Get all open positions."""
    return await get_user_positions(db, user.id)