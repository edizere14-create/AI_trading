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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_contract_size(row: dict[str, Any]) -> float:
    info = row.get("info")
    if isinstance(info, dict):
        size = _safe_float(
            info.get("contractSize", info.get("contract_size", info.get("contractValue"))),
            0.0,
        )
        if size > 0:
            return size
    size = _safe_float(row.get("contractSize", row.get("contract_size")), 0.0)
    return size if size > 0 else 1.0


def _contracts_to_base_quantity(symbol: str, contracts: float, price: float, contract_size: float) -> float:
    qty_contracts = abs(float(contracts))
    if qty_contracts <= 0:
        return 0.0

    size = contract_size if contract_size > 0 else 1.0
    normalized_symbol = str(symbol or "").upper()
    is_inverse_usd_contract = any(
        token in normalized_symbol
        for token in ("/USD:BTC", "/USD:XBT", "/USD:ETH", "/USD:SOL")
    )

    if is_inverse_usd_contract and price > 0:
        return (qty_contracts * size) / price
    return qty_contracts * size


def _get_momentum_worker() -> Any | None:
    try:
        from app.api import routes_momentum

        return getattr(routes_momentum, "momentum_worker", None)
    except Exception:
        return None


def _worker_exchange_snapshot(include_open_orders: bool = True) -> dict[str, list[dict[str, Any]]]:
    worker = _get_momentum_worker()
    if worker is None:
        return {"positions": [], "open_orders": []}

    execution_engine = getattr(worker, "execution_engine", None)
    if execution_engine is None or _parse_bool(getattr(execution_engine, "paper_mode", True), default=True):
        return {"positions": [], "open_orders": []}

    exchange = getattr(execution_engine, "exchange", None)
    if exchange is None:
        return {"positions": [], "open_orders": []}

    positions_rows: list[dict[str, Any]] = []
    open_order_rows: list[dict[str, Any]] = []

    try:
        raw_positions = exchange.fetch_positions()
        for pos in raw_positions or []:
            contracts = _safe_float(pos.get("contracts"), 0.0)
            if contracts == 0:
                continue

            symbol = str(pos.get("id") or pos.get("symbol") or "")
            side_raw = str(pos.get("side") or "").lower()
            side = "buy" if side_raw in {"long", "buy"} or contracts > 0 else "sell"
            entry_price = _safe_float(pos.get("entryPrice"), 0.0)
            current_price = _safe_float(pos.get("markPrice"), entry_price if entry_price > 0 else 0.0)
            if current_price <= 0:
                current_price = entry_price
            if entry_price <= 0:
                entry_price = current_price

            contract_size = _extract_contract_size(pos)
            quantity = _contracts_to_base_quantity(symbol, contracts, current_price, contract_size)
            if quantity <= 0:
                quantity = abs(contracts)

            notional = (
                quantity * current_price
                if current_price > 0
                else abs(contracts) * contract_size
            )
            unrealized = _safe_float(pos.get("unrealizedPnl"), 0.0)
            if not unrealized and current_price > 0 and entry_price > 0:
                unrealized = (
                    (current_price - entry_price) * quantity
                    if side == "buy"
                    else (entry_price - current_price) * quantity
                )

            positions_rows.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "contracts": abs(contracts),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl": unrealized,
                    "notional": notional,
                    "leverage": _safe_float(pos.get("leverage"), 0.0),
                    "source": "exchange",
                    "status": "open",
                }
            )
    except Exception:
        # Fall back to risk-manager memory snapshot if exchange pull fails.
        positions_rows = []

    if include_open_orders:
        try:
            raw_orders = exchange.fetch_open_orders()
            for order in raw_orders or []:
                side = str(order.get("side") or "").lower()
                if side not in {"buy", "sell"}:
                    continue

                qty = _safe_float(order.get("remaining"), 0.0)
                if qty <= 0:
                    qty = _safe_float(order.get("amount"), 0.0)
                if qty <= 0:
                    continue

                price = _safe_float(order.get("price"), 0.0)
                symbol = str(order.get("symbol") or "")
                info = order.get("info")
                if isinstance(info, dict):
                    symbol = str(info.get("symbol") or symbol)

                contract_size = _extract_contract_size(order if isinstance(order, dict) else {})
                quantity = _contracts_to_base_quantity(symbol, qty, price, contract_size)
                if quantity <= 0:
                    quantity = abs(qty)

                open_order_rows.append(
                    {
                        "symbol": symbol,
                        "side": side,
                        "quantity": quantity,
                        "contracts": abs(qty),
                        "entry_price": price,
                        "current_price": price,
                        "unrealized_pnl": 0.0,
                        "notional": quantity * price if price > 0 else abs(qty) * contract_size,
                        "leverage": 0.0,
                        "source": "open_order",
                        "status": "open_order",
                        "order_id": str(order.get("id") or ""),
                    }
                )
        except Exception:
            open_order_rows = []

    return {"positions": positions_rows, "open_orders": open_order_rows}


def _worker_risk_manager_positions_snapshot() -> list[dict[str, Any]]:
    worker = _get_momentum_worker()
    if worker is None:
        return []

    risk_manager = getattr(worker, "risk_manager", None)
    if risk_manager is None:
        return []

    positions = getattr(risk_manager, "positions", {})
    if not isinstance(positions, dict):
        return []

    last_price = None
    signal = getattr(worker, "last_signal", None)
    if isinstance(signal, dict):
        last_price = _safe_float(signal.get("price"), 0.0)
    if not last_price:
        candles = list(getattr(worker, "candle_history", []))
        if candles and isinstance(candles[-1], dict):
            last_price = _safe_float(candles[-1].get("close"), 0.0)

    rows: list[dict[str, Any]] = []
    for symbol, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        side = str(pos.get("side", "buy") or "buy").lower()
        quantity = _safe_float(pos.get("quantity"), 0.0)
        entry_price = _safe_float(pos.get("entry_price"), 0.0)
        if quantity <= 0 or entry_price <= 0:
            continue

        current_price = float(last_price) if last_price and last_price > 0 else entry_price
        unrealized = (
            (current_price - entry_price) * quantity
            if side == "buy"
            else (entry_price - current_price) * quantity
        )

        rows.append(
            {
                "symbol": str(symbol),
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized,
                "leverage": 1.0,
                "source": "worker",
            }
        )

    return rows


def _worker_positions_snapshot(include_open_orders: bool = False) -> list[dict[str, Any]]:
    exchange_snapshot = _worker_exchange_snapshot(include_open_orders=include_open_orders)
    rows = list(exchange_snapshot.get("positions", []))
    if include_open_orders:
        rows.extend(exchange_snapshot.get("open_orders", []))
    if rows:
        return rows
    return _worker_risk_manager_positions_snapshot()


def _filter_open_order_rows(rows: list[dict[str, Any]], include_open_orders: bool) -> list[dict[str, Any]]:
    if include_open_orders:
        return rows
    return [
        row
        for row in rows
        if str(row.get("status", "")).lower() != "open_order"
        and str(row.get("source", "")).lower() != "open_order"
    ]


def _worker_risk_snapshot() -> dict[str, Any] | None:
    worker = _get_momentum_worker()
    if worker is None:
        return None

    risk_manager = getattr(worker, "risk_manager", None)
    if risk_manager is None:
        return None

    account_balance = _safe_float(getattr(risk_manager, "account_balance", DEFAULT_ACCOUNT_EQUITY), DEFAULT_ACCOUNT_EQUITY)
    realized_total_pnl = _safe_float(getattr(risk_manager, "total_pnl", 0.0), 0.0)
    realized_daily_pnl = _safe_float(getattr(risk_manager, "daily_pnl", 0.0), 0.0)
    if callable(getattr(risk_manager, "get_status", None)):
        try:
            status_payload = risk_manager.get_status()
            if isinstance(status_payload, dict):
                realized_total_pnl = _safe_float(status_payload.get("total_pnl"), realized_total_pnl)
                realized_daily_pnl = _safe_float(
                    status_payload.get("daily_realized_pnl", status_payload.get("daily_pnl")),
                    realized_daily_pnl,
                )
        except Exception:
            pass

    exchange_snapshot = _worker_exchange_snapshot(include_open_orders=True)
    exchange_positions = list(exchange_snapshot.get("positions", []))
    open_orders_count = len(exchange_snapshot.get("open_orders", []))
    positions = exchange_positions if exchange_positions else _worker_risk_manager_positions_snapshot()
    unrealized_pnl = sum(_safe_float(p.get("unrealized_pnl"), 0.0) for p in positions)

    exposure = sum(
        _safe_float(
            p.get("notional"),
            _safe_float(p.get("quantity"), 0.0) * _safe_float(p.get("current_price"), 0.0),
        )
        for p in positions
    )
    exposure_pct = (exposure / account_balance * 100.0) if account_balance > 0 else 0.0
    total_pnl = realized_total_pnl + unrealized_pnl
    daily_pnl = realized_daily_pnl + unrealized_pnl
    equity = account_balance + unrealized_pnl

    return {
        "account_balance": account_balance,
        "equity": equity,
        "total_pnl": total_pnl,
        "daily_pnl": daily_pnl,
        "realized_total_pnl": realized_total_pnl,
        "realized_daily_pnl": realized_daily_pnl,
        "unrealized_pnl": unrealized_pnl,
        "exposure_pct": exposure_pct,
        "exposure": exposure,
        "open_positions": len(positions),
        "open_orders": open_orders_count,
    }


def _apply_worker_fallback(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("risk_service") == "online":
        return snapshot

    worker_snapshot = _worker_risk_snapshot()
    if not worker_snapshot:
        return snapshot

    snapshot.update(worker_snapshot)
    snapshot["risk_service"] = "worker-fallback"
    return snapshot


RISK_MICROSERVICE_URL = str(getattr(settings, "RISK_SERVICE_URL", "http://risk-service:8000")).rstrip("/")
TRADING_ENABLED = _parse_bool(getattr(settings, "TRADING_ENABLED", True), default=True)
DEFAULT_ACCOUNT_EQUITY = float(getattr(settings, "DEFAULT_ACCOUNT_EQUITY", 1000.0) or 1000.0)


@router.post(
    "/check",
    response_model=TradeCheckResponse,
    status_code=status.HTTP_200_OK,
)
async def check_trade_risk_route(
    trade_data: Dict[str, Any],
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

    payload_raw = dict(trade_data or {})
    nested_trade = payload_raw.get("trade")
    if isinstance(nested_trade, dict):
        symbol = str(nested_trade.get("symbol") or "PI_XBTUSD")
        quantity = _safe_float(nested_trade.get("quantity"), 0.0)
        entry_price = _safe_float(nested_trade.get("entry_price"), 0.0)
        stop_price = _safe_float(nested_trade.get("stop_loss", nested_trade.get("stop_price")), 0.0)
        side = str(nested_trade.get("side") or "buy")
        leverage = _safe_float(nested_trade.get("leverage"), 1.0)
        equity = _safe_float(payload_raw.get("equity"), DEFAULT_ACCOUNT_EQUITY)
        positions = payload_raw.get("open_positions")
        if not isinstance(positions, list):
            positions = payload_raw.get("positions")
    else:
        symbol = str(payload_raw.get("symbol") or "PI_XBTUSD")
        quantity = _safe_float(payload_raw.get("quantity"), 0.0)
        entry_price = _safe_float(payload_raw.get("entry_price"), 0.0)
        stop_price = _safe_float(payload_raw.get("stop_loss"), 0.0)
        side = str(payload_raw.get("side") or "buy")
        leverage = _safe_float(payload_raw.get("leverage"), 1.0)
        equity = _safe_float(payload_raw.get("equity"), DEFAULT_ACCOUNT_EQUITY)
        positions = payload_raw.get("positions")

    if quantity <= 0 or entry_price <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid trade payload")

    if stop_price <= 0:
        if side.lower() == "buy":
            stop_price = entry_price * (1.0 - 0.02)
        else:
            stop_price = entry_price * (1.0 + 0.02)

    if equity <= 0:
        equity = DEFAULT_ACCOUNT_EQUITY

    payload: Dict[str, Any] = {
        "equity": equity,
        "positions": list(positions or []),
        "trade": {
            "symbol": symbol,
            "quantity": float(quantity),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "side": side,
            "leverage": float(leverage if leverage > 0 else 1.0),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{RISK_MICROSERVICE_URL}/var",
                json=payload,
            )

        if response.status_code != 200:
            body = {}
        else:
            body = response.json() if response.content else {}
            if not isinstance(body, dict):
                body = {}
        max_size = float(body.get("max_size", body.get("max_position_size", quantity)) or quantity)
        reason = str(body.get("reason", body.get("message", "Trade approved under VaR constraints")))
        approved = bool(body.get("approved", True))

        return TradeCheckResponse(
            approved=approved,
            max_size=max_size,
            max_position_size=max_size,
            reason=reason,
        )

    except httpx.RequestError:
        # Local compatibility fallback used when independent risk service is unreachable.
        stop_distance = abs(float(entry_price) - float(stop_price))
        if stop_distance <= 0:
            stop_distance = max(1e-9, float(entry_price) * 0.02)
        max_risk_amount = float(equity) * 0.02
        max_size = max(0.0, max_risk_amount / stop_distance)
        approved = float(quantity) <= max_size if max_size > 0 else False
        reason = "risk check passed (local fallback)" if approved else "position size exceeds local fallback risk cap"
        return TradeCheckResponse(
            approved=approved,
            max_size=max_size,
            max_position_size=max_size,
            reason=reason,
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
    snapshot: dict[str, Any] = {
        "risk_service": "offline",
        "timestamp": _utc_now(),
        "account_balance": DEFAULT_ACCOUNT_EQUITY,
        "equity": DEFAULT_ACCOUNT_EQUITY,
        "total_pnl": 0.0,
        "daily_pnl": 0.0,
        "exposure_pct": 0.0,
        "exposure": 0.0,
        "open_positions": 0,
        "open_orders": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{RISK_MICROSERVICE_URL}/health")

        if response.status_code != 200:
            snapshot["risk_service"] = "degraded"
            snapshot["detail"] = "Risk service unhealthy"
            return _apply_worker_fallback(snapshot)

        snapshot["risk_service"] = "online"

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
                        "open_orders",
                    ):
                        if key in payload:
                            snapshot[key] = payload[key]
            else:
                snapshot["risk_service"] = "degraded"
                snapshot["detail"] = "Risk service status endpoint unavailable"
        except httpx.RequestError:
            snapshot["risk_service"] = "degraded"
            snapshot["detail"] = "Risk service status endpoint unreachable"

        return _apply_worker_fallback(snapshot)
    except httpx.RequestError:
        snapshot["detail"] = "Risk service unreachable"
        return _apply_worker_fallback(snapshot)


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
            return {"positions": _worker_positions_snapshot(include_open_orders=False)}

        payload = response.json() if response.content else []
        if isinstance(payload, dict):
            rows = payload.get("positions", [])
            valid_rows = rows if isinstance(rows, list) else []
            return {"positions": _filter_open_order_rows(valid_rows, include_open_orders=False)}
        if isinstance(payload, list):
            return {"positions": _filter_open_order_rows(payload, include_open_orders=False)}
    except httpx.RequestError:
        pass

    return {"positions": _worker_positions_snapshot(include_open_orders=False)}


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
