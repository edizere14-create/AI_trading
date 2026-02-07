# FastAPI endpoint for placing live orders
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.trade import PlaceOrderRequest, OrderResponse, OrderHistory
from app.services.trade_service import place_order, get_user_orders
from app.services.broker_service import get_broker_for_user
from app.utils.dependencies import get_db_dep, get_current_user
from app.db.models.user import User

router = APIRouter()

@router.post("/order", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def place_order_endpoint(
    order: PlaceOrderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> OrderResponse:
    """Place new trade order."""
    try:
        return await place_order(db, user.id, order)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to place order",
        )

@router.get("/orders", response_model=list[OrderHistory])
async def get_orders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> list[OrderHistory]:
    """Get user's order history."""
    return await get_user_orders(db, user.id if isinstance(user.id, int) else int(user.id))
