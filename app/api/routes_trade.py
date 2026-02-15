# FastAPI endpoint for placing live orders
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, List

from app.schemas.trade import PlaceOrderRequest, OrderResponse, OrderHistory
from app.services.trade_service import place_order, get_user_orders
from app.services.broker_service import get_broker_for_user
from app.utils.dependencies import get_db_dep, get_current_user
from app.db.models.user import User
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/order", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def place_order_endpoint(
    order: PlaceOrderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> OrderResponse:
    """Place new trade order."""
    try:
        if user is None or user.id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        return await place_order(db, int(user.id), order)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to place order")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to place order",
        )

@router.get("/orders", response_model=List[OrderHistory])
async def get_orders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> List[OrderHistory]:
    """Get user's order history."""
    try:
        if user is None or user.id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        return await get_user_orders(db, int(user.id))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to fetch order history")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch order history",
        )

@router.get("/health")
async def trade_health_check() -> Dict[str, str]:
    """Health check for trading routes with error reporting."""
    try:
        return {
            "status": "ok",
            "service": "trade",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.exception("Trade health check failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Trade health check failed: {e}",
        )
