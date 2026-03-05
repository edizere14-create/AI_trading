from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError, Field
from typing import Dict, Any
import logging
import hmac
import hashlib
import os
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class TradingViewSignal(BaseModel):
    """TradingView webhook signal schema"""
    symbol: str | None = None
    ticker: str | None = None
    action: str  # "buy", "sell", "close"
    price: float | None = None
    size: float | None = None
    timestamp: str | None = None
    passphrase: str | None = Field(default=None, repr=False)


class DeribitSignal(BaseModel):
    """Deribit webhook signal schema"""
    instrument: str
    direction: str  # "call", "put"
    strike: float
    expiration: str


async def verify_webhook_signature(request: Request, secret: str) -> bool:
    """Verify webhook signature (TradingView)"""
    signature = request.headers.get("X-Webhook-Signature")
    if not signature:
        return False
    
    body = await request.body()
    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)


def _log_event(level: int, event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    logger.log(level, json.dumps(payload, default=str, ensure_ascii=False))


def _client_ip(request: Request) -> str:
    forwarded_for = str(request.headers.get("x-forwarded-for", "")).strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def _normalized_symbol(signal: TradingViewSignal) -> str:
    preferred = signal.symbol or signal.ticker
    if preferred is None:
        return ""
    return str(preferred).strip().upper()


@router.post("/tradingview")
async def receive_tradingview_signal(request: Request) -> dict[str, Any]:
    """
    Receive signals from TradingView alerts
    
    Example webhook URL:
    https://yourdomain.com/api/webhooks/tradingview
    
    TradingView Alert Message:
    {
        "symbol": "XRPUSD",
        "action": "buy",
        "price": 2.45,
        "timestamp": "2026-02-18T21:15:00Z"
    }
    """
    client_ip = _client_ip(request)
    raw_payload: dict[str, Any]

    try:
        parsed_json = await request.json()
    except Exception as exc:
        _log_event(logging.ERROR, "webhook_parse_error", source="tradingview", reason="invalid_json", ip=client_ip, error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    if not isinstance(parsed_json, dict):
        _log_event(logging.ERROR, "webhook_validation_error", source="tradingview", reason="payload_not_object", ip=client_ip)
        raise HTTPException(status_code=422, detail="Webhook payload must be a JSON object")

    raw_payload = parsed_json

    try:
        signal = TradingViewSignal.model_validate(raw_payload)
    except ValidationError as exc:
        _log_event(logging.ERROR, "webhook_validation_error", source="tradingview", reason="schema_validation_failed", ip=client_ip, errors=exc.errors())
        raise HTTPException(status_code=422, detail="Invalid TradingView payload schema") from exc

    secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
    if secret and not await verify_webhook_signature(request, secret):
        _log_event(logging.WARNING, "webhook_auth_failed", source="tradingview", reason="invalid_signature", ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid signature")

    webhook_passphrase = os.getenv("WEBHOOK_PASSPHRASE", "").strip()
    if webhook_passphrase and (signal.passphrase or "") != webhook_passphrase:
        _log_event(logging.WARNING, "webhook_auth_failed", source="tradingview", reason="invalid_passphrase", ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    symbol = _normalized_symbol(signal)
    action = str(signal.action or "").strip().lower()
    if action not in {"buy", "sell", "close"}:
        _log_event(logging.ERROR, "webhook_execution_rejected", source="tradingview", reason="unsupported_action", ip=client_ip, action=action, symbol=symbol)
        raise HTTPException(status_code=422, detail="action must be one of: buy, sell, close")

    if not symbol:
        _log_event(logging.ERROR, "webhook_execution_rejected", source="tradingview", reason="missing_symbol", ip=client_ip, action=action)
        raise HTTPException(status_code=422, detail="symbol or ticker is required")

    try:
        _log_event(
            logging.INFO,
            "webhook_signal_received",
            source="tradingview",
            ip=client_ip,
            action=action,
            symbol=symbol,
            size=signal.size,
            price=signal.price,
        )
        return {
            "status": "received",
            "symbol": symbol,
            "action": action,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _log_event(logging.ERROR, "webhook_execution_error", source="tradingview", reason="unexpected_exception", ip=client_ip, action=action, symbol=symbol, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/metrics")
async def expose_metrics() -> Any:
    """
    Prometheus metrics endpoint
    Access at: http://localhost:8000/api/webhooks/metrics
    """
    from fastapi.responses import Response

    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )
    except ModuleNotFoundError:
        return Response(content="# prometheus_client not installed\n", media_type="text/plain")