from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.risk import TradeCheckRequest, TradeCheckResponse, RiskLimits
from app.services.risk_service import check_trade_risk, get_user_risk_limits
from app.utils.dependencies import get_db_dep, get_current_user
from app.db.models.user import User

router = APIRouter()

@router.post("/check", response_model=TradeCheckResponse)
async def check_risk_trade(
    trade_data: TradeCheckRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> TradeCheckResponse:
    """Validate trade against user risk limits."""
    try:
        return await check_trade_risk(db, user.id, trade_data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

@router.get("/limits", response_model=RiskLimits)
async def get_risk_limits(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> RiskLimits:
    """Get user's risk preferences."""
    return await get_user_risk_limits(db, user.id)