from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.models.user import User
from app.schemas.risk import TradeCheckRequest, TradeCheckResponse, RiskLimits
from app.services.audit_service import log_risk_event
from app.services.risk_local_service import get_user_risk_limits
from app.utils.dependencies import get_current_user, get_db_dep

router = APIRouter(
    prefix="/risk",
    tags=["Institutional Risk Layer"],
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


RISK_MICROSERVICE_URL = str(getattr(settings, "RISK_SERVICE_URL", "http://risk-service:8000")).rstrip("/")
TRADING_ENABLED = _parse_bool(getattr(settings, "TRADING_ENABLED", True), default=True)
DEFAULT_ACCOUNT_EQUITY = float(getattr(settings, "DEFAULT_ACCOUNT_EQUITY", 1000.0) or 1000.0)


@router.post(
    "/check",
    response_model=TradeCheckResponse,
    status_code=status.HTTP_200_OK,
)
async def check_trade_risk_route(
    trade_data: TradeCheckRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> TradeCheckResponse:
    """
    Institutional Pre-Trade Risk Gateway

    Validates with independent risk service:
    - VaR limit
    - Exposure cap
    - Daily drawdown
    - Leverage cap
    - Circuit breaker
    """

    if not TRADING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading globally disabled",
        )

    equity = float(getattr(trade_data, "account_equity", 0.0) or 0.0)
    if equity <= 0:
        equity = DEFAULT_ACCOUNT_EQUITY

    payload: Dict[str, Any] = {
        "equity": equity,
        "positions": list(getattr(trade_data, "positions", []) or []),
        "trade": {
            "symbol": trade_data.symbol,
            "quantity": float(trade_data.quantity),
            "entry_price": float(trade_data.entry_price),
            "stop_price": float(trade_data.stop_loss),
            "side": str(getattr(trade_data, "side", "buy") or "buy"),
            "leverage": float(getattr(trade_data, "leverage", 1.0) or 1.0),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{RISK_MICROSERVICE_URL}/var",
                json=payload,
            )

        if response.status_code != 200:
            await log_risk_event(
                db=db,
                user_id=user.id,
                event_type="TRADE_REJECTED_RISK",
                metadata={
                    "reason": response.text,
                    "symbol": trade_data.symbol,
                    "timestamp": _utc_now(),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Risk validation failed",
            )

        body = response.json() if response.content else {}
        if not isinstance(body, dict):
            body = {}

        await log_risk_event(
            db=db,
            user_id=user.id,
            event_type="TRADE_CHECK_PASSED",
            metadata={
                "symbol": trade_data.symbol,
                "quantity": trade_data.quantity,
                "var_percent": body.get("var_percent"),
                "timestamp": _utc_now(),
            },
        )

        max_size = float(body.get("max_size", body.get("max_position_size", trade_data.quantity)) or trade_data.quantity)
        reason = str(body.get("message", "Trade approved under VaR constraints"))

        return TradeCheckResponse(
            approved=True,
            max_size=max_size,
            reason=reason,
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Risk service unavailable",
        )


@router.get(
    "/limits",
    response_model=RiskLimits,
    status_code=status.HTTP_200_OK,
)
async def get_risk_limits_route(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> RiskLimits:
    """
    Returns:
    - Max risk per trade
    - Max daily drawdown
    - Max exposure %
    - Max leverage
    """
    return await get_user_risk_limits(db, user.id)


@router.get(
    "/status",
    status_code=status.HTTP_200_OK,
)
async def get_risk_status_route() -> dict[str, Any]:
    """
    Fetches real-time risk-service health + dashboard-compatible snapshot fields.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{RISK_MICROSERVICE_URL}/health")

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Risk service unhealthy",
            )

        snapshot: dict[str, Any] = {
            "risk_service": "online",
            "timestamp": _utc_now(),
            "account_balance": DEFAULT_ACCOUNT_EQUITY,
            "equity": DEFAULT_ACCOUNT_EQUITY,
            "total_pnl": 0.0,
            "daily_pnl": 0.0,
            "exposure_pct": 0.0,
            "exposure": 0.0,
            "open_positions": 0,
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                metrics_response = await client.get(f"{RISK_MICROSERVICE_URL}/status")
            if metrics_response.status_code == 200:
                payload = metrics_response.json() if metrics_response.content else {}
                if isinstance(payload, dict):
                    for key in (
                        "account_balance",
                        "equity",
                        "total_pnl",
                        "daily_pnl",
                        "exposure_pct",
                        "exposure",
                        "open_positions",
                    ):
                        if key in payload:
                            snapshot[key] = payload[key]
        except httpx.RequestError:
            pass

        return snapshot
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Risk service unreachable",
        )


@router.get(
    "/positions",
    status_code=status.HTTP_200_OK,
)
async def get_risk_positions_route() -> dict[str, list[dict[str, Any]]]:
    """
    Dashboard-compatible positions endpoint.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{RISK_MICROSERVICE_URL}/positions")
        if response.status_code != 200:
            return {"positions": []}

        payload = response.json() if response.content else []
        if isinstance(payload, dict):
            rows = payload.get("positions", [])
            return {"positions": rows if isinstance(rows, list) else []}
        if isinstance(payload, list):
            return {"positions": payload}
    except httpx.RequestError:
        pass

    return {"positions": []}


@router.post(
    "/close-all",
    status_code=status.HTTP_200_OK,
)
async def emergency_close_all_route(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> dict[str, Any]:
    """
    Institutional emergency liquidation endpoint.

    Requirements:
    - ADMIN or TRADER role
    - Logged in audit trail
    """

    role = str(getattr(user, "role", "TRADER")).upper()
    if role not in {"ADMIN", "TRADER"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    if not TRADING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading disabled",
        )

    await log_risk_event(
        db=db,
        user_id=user.id,
        event_type="EMERGENCY_CLOSE_ALL_TRIGGERED",
        metadata={
            "timestamp": _utc_now(),
        },
    )

    return {
        "status": "accepted",
        "detail": "Emergency liquidation request registered",
        "closed_count": 0,
    }
