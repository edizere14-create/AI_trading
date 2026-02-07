from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from app.schemas.trade import PlaceOrderRequest, OrderResponse, OrderHistory
from app.services.broker_service import get_broker_for_user
from app.db.models.trade import Order  # Assuming you have this model

async def place_order(
    db: AsyncSession,
    user_id: int,
    order_request: PlaceOrderRequest,
) -> OrderResponse:
    """Place a new order via broker."""
    broker = await get_broker_for_user(db, user_id)
    
    try:
        result = await broker.place_order(
            symbol=order_request.symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            price=order_request.price or 0.0,
            order_type=order_request.order_type,
        )
        
        # Save order to database
        order = Order(
            user_id=user_id,
            order_id=result.get("order_id"),
            symbol=order_request.symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            price=order_request.price,
            status="pending",
            created_at=datetime.utcnow(),
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)
        
        return OrderResponse.from_orm(order)
    except Exception as e:
        raise ValueError(f"Failed to place order: {str(e)}")

async def get_user_orders(db: AsyncSession, user_id: int) -> list[OrderHistory]:
    """Fetch user's order history."""
    stmt = select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
    result = await db.execute(stmt)
    orders = result.scalars().all()
    return [OrderHistory.from_orm(o) for o in orders]