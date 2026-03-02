from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import logging

import asyncio
from app.schemas.trade import PlaceOrderRequest, OrderResponse, OrderHistory
from app.services.broker_service import get_broker_for_user
from app.services.execution_log_service import log_execution_event
from app.db.models.trade import Order, OrderStatus
from app.db.async_database import AsyncSessionLocal

logger = logging.getLogger(__name__)

async def place_order(
    db: AsyncSession,
    user_id: int,
    order_request: PlaceOrderRequest,
) -> OrderResponse:
    """Place a new order via broker."""
    broker = await get_broker_for_user(db, user_id)
    
    try:
        result = None
        for attempt in range(1, 4):
            result = await broker.place_order(
                symbol=order_request.symbol,
                side=order_request.side,
                quantity=order_request.quantity,
                price=order_request.price or None,
                order_type=order_request.order_type,
                order_kind=order_request.order_kind,
                expected_price=order_request.expected_price or order_request.price,
            )
            status = (result or {}).get("status", "pending")
            if status in ("filled", "partial"):
                break
            await asyncio.sleep(0.5)
        
        metrics = (result or {}).get("metrics", {})
        filled_qty = (result or {}).get("filled_quantity")
        avg_fill = (result or {}).get("avg_fill_price")

        order = Order(
            user_id=user_id,
            order_id=(result or {}).get("order_id"),
            symbol=order_request.symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            price=order_request.price,
            order_type=order_request.order_type,
            order_kind=order_request.order_kind,
            status=(result or {}).get("status", "pending"),
            filled_quantity=filled_qty or 0.0,
            avg_fill_price=avg_fill,
            slippage=metrics.get("slippage"),
            fill_rate=metrics.get("fill_rate"),
            latency_ms=metrics.get("latency_ms"),
            created_at=datetime.utcnow(),
            filled_at=(datetime.utcnow() if (result or {}).get("status") in ("filled", "partial") else None),
        )
        db.add(order)

        await log_execution_event(
            db,
            user_id=user_id,
            event_type="order_submitted",
            symbol=order_request.symbol,
            side=str(order_request.side),
            quantity=float(order_request.quantity),
            price=float(order_request.price) if order_request.price is not None else None,
            status=str((result or {}).get("status", "pending")),
            details={
                "order_id": (result or {}).get("order_id"),
                "metrics": metrics,
            },
        )

        await db.commit()
        await db.refresh(order)
        
        return OrderResponse.from_orm(order)
    except Exception as e:
        logger.exception("Failed to place order for user_id=%s", user_id)
        try:
            await db.rollback()
            await log_execution_event(
                db,
                user_id=user_id,
                event_type="order_failed",
                symbol=order_request.symbol,
                side=str(order_request.side),
                quantity=float(order_request.quantity),
                price=float(order_request.price) if order_request.price is not None else None,
                status="error",
                details={"error": str(e)},
            )
            await db.commit()
        except Exception:
            await db.rollback()
        raise ValueError(f"Failed to place order: {str(e)}")

async def get_user_orders(db: AsyncSession, user_id: int) -> list[OrderHistory]:
    """Fetch user's order history."""
    stmt = select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
    result = await db.execute(stmt)
    orders = result.scalars().all()
    return [OrderHistory.from_orm(o) for o in orders]

async def refresh_open_orders(db: AsyncSession) -> None:
    stmt = select(Order).where(Order.status.in_([OrderStatus.PENDING, OrderStatus.PARTIAL]))
    result = await db.execute(stmt)
    orders = result.scalars().all()

    for order in orders:
        if not getattr(order, "order_id", None):
            continue

        broker = await get_broker_for_user(db, order.user_id)
        try:
            status_result = await broker.get_order_status(order.order_id)
        except Exception:
            continue

        new_status = (status_result or {}).get("status")
        if not new_status:
            continue

        if new_status == "open":
            new_status = "pending"

        filled_qty = (status_result or {}).get("filled_quantity")
        avg_fill = (status_result or {}).get("avg_fill_price")

        trade = (status_result or {}).get("trade") or {}
        if filled_qty is None:
            filled_qty = trade.get("qty") or trade.get("filled_quantity")
        if avg_fill is None:
            avg_fill = trade.get("price") or trade.get("avg_fill_price")

        order.status = new_status
        if filled_qty is not None:
            order.filled_quantity = filled_qty
        if avg_fill is not None:
            order.avg_fill_price = avg_fill
        if new_status in ("filled", "partial"):
            order.filled_at = datetime.utcnow()

        await log_execution_event(
            db,
            user_id=int(order.user_id),
            event_type="order_status_update",
            symbol=str(order.symbol),
            side=str(order.side),
            quantity=float(order.quantity),
            price=float(order.avg_fill_price) if order.avg_fill_price is not None else None,
            status=str(new_status),
            details={"order_id": order.order_id},
        )

    await db.commit()

async def poll_order_updates(interval_sec: float = 5.0) -> None:
    while True:
        async with AsyncSessionLocal() as db:
            await refresh_open_orders(db)
        await asyncio.sleep(interval_sec)