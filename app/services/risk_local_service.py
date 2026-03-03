from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.risk import RiskLimits
from app.services.risk_service import get_user_risk_limits as _get_user_risk_limits


async def get_user_risk_limits(db: AsyncSession, user_id: int) -> RiskLimits:
    return await _get_user_risk_limits(db, user_id)
