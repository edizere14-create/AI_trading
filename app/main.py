from __future__ import annotations
# pyright: reportUntypedFunctionDecorator=false
# mypy: disable-error-code=misc

import asyncio
import logging
import os
import requests
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Response, status, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core import logging_config
from app.services.websocket_service import ConnectionManager
from app.services.startup_safety import startup_safety_check
from app.strategy_manager import StrategyManager
from app.brokers.kraken import KrakenBroker
from app.utils.ai_models import TradingAIModels
from app.api import routes_auth, routes_users, routes_portfolio, routes_trade, routes_data, routes_risk, routes_indicators, routes_strategy, routes_backtest, routes_webhooks, routes_momentum

_setup_logging = getattr(logging_config, "setup_logging", None) or getattr(logging_config, "configure_logging", None)
if callable(_setup_logging):
    _setup_logging(settings.LOG_LEVEL)
else:
    logging.basicConfig(level=getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO))

logger = logging.getLogger(__name__)

models: TradingAIModels | None = None
manager: ConnectionManager = ConnectionManager()
strategy_manager: StrategyManager = StrategyManager()
momentum_worker: Any | None = None
momentum_task: asyncio.Task[Any] | None = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sandbox_mode_from_env(default: bool = True) -> bool:
    sandbox_raw = os.getenv("KRAKEN_SANDBOX")
    if sandbox_raw is not None:
        return sandbox_raw.strip().lower() in {"1", "true", "yes", "on"}
    return _env_bool("KRAKEN_FUTURES_DEMO", default)

# Initialize Kraken broker
kraken_broker = KrakenBroker(
    api_key=os.getenv("KRAKEN_API_KEY", ""),
    api_secret=os.getenv("KRAKEN_API_SECRET", "")
)


def _futures_symbol_candidates(symbol: str) -> list[str]:
    raw = (symbol or "PI_XBTUSD").strip().upper()
    candidates = [raw]
    if raw.startswith("PI_"):
        candidates.append(raw.replace("PI_", "PF_", 1))
    elif raw.startswith("PF_"):
        candidates.append(raw.replace("PF_", "PI_", 1))
    elif raw in {"BTCUSD", "XBTUSD"}:
        candidates.extend(["PI_XBTUSD", "PF_XBTUSD"])
    return list(dict.fromkeys(candidates))


async def _fetch_kraken_public_price(symbol: str) -> float:
    url = os.getenv("KRAKEN_BASE_URL", "https://demo-futures.kraken.com/derivatives/api/v3/").strip()
    base = url.split("/derivatives/api/v3", 1)[0].rstrip("/")
    endpoint = f"{base}/derivatives/api/v3/tickers"
    candidates = _futures_symbol_candidates(symbol)

    def _request() -> float:
        response = requests.get(endpoint, timeout=4)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict):
            return 0.0
        tickers = payload.get("tickers")
        if not isinstance(tickers, list):
            return 0.0

        for candidate in candidates:
            for ticker in tickers:
                if not isinstance(ticker, dict):
                    continue
                if str(ticker.get("symbol", "")).upper() != candidate:
                    continue
                for key in ("markPrice", "last", "lastTradePrice", "indexPrice"):
                    try:
                        price = float(ticker.get(key) or 0.0)
                    except (TypeError, ValueError):
                        price = 0.0
                    if price > 0:
                        return price
        return 0.0

    return await asyncio.to_thread(_request)


async def _fetch_kraken_spot_price(symbol: str) -> float:
    symbol_upper = str(symbol or "").upper()
    if "ETH" in symbol_upper:
        pair = "ETHUSD"
    elif "SOL" in symbol_upper:
        pair = "SOLUSD"
    else:
        pair = "XBTUSD"

    endpoint = "https://api.kraken.com/0/public/Ticker"

    def _request() -> float:
        response = requests.get(endpoint, params={"pair": pair}, timeout=4)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict) or payload.get("error"):
            return 0.0
        result = payload.get("result")
        if not isinstance(result, dict) or not result:
            return 0.0
        ticker = next(iter(result.values()))
        if not isinstance(ticker, dict):
            return 0.0
        last = ticker.get("c")
        if isinstance(last, list) and last:
            try:
                px = float(last[0])
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                return px
        return 0.0

    return await asyncio.to_thread(_request)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup/shutdown."""
    global models, momentum_worker, momentum_task
    
    # Startup
    logger.info("Starting AI Trading application...")
    try:
        models = TradingAIModels()
        logger.info("AI models initialized")
    except Exception as exc:
        models = None
        logger.warning("AI models unavailable, continuing without them: %s", exc)

    try:
        strategy_manager.create_rsi_strategy("XXBTZUSD", overbought=70, oversold=30)
        strategy_manager.create_rsi_strategy("XETHZUSD", overbought=70, oversold=30)
        logger.info("Trading strategies initialized")
    except Exception as exc:
        logger.warning("Failed to initialize default strategies: %s", exc)

    try:
        from app.services.data_service import DataService
        from engine.core.execution_engine import ExecutionEngine
        from engine.workers.momentum_worker import MomentumWorker

        paper_mode = _env_bool("TRADING_PAPER_MODE", True)
        sandbox_mode = _sandbox_mode_from_env(True)
        api_key = os.getenv("KRAKEN_API_KEY", "")
        api_secret = os.getenv("KRAKEN_API_SECRET", "")
        if not paper_mode and (not api_key or not api_secret):
            logger.warning(
                "TRADING_PAPER_MODE=false but KRAKEN_API_KEY/KRAKEN_API_SECRET missing; falling back to paper mode"
            )
            paper_mode = True

        execution_engine = ExecutionEngine(
            exchange_id="krakenfutures",
            api_key=api_key,
            api_secret=api_secret,
            paper_mode=paper_mode,
            sandbox=sandbox_mode,
        )
        logger.info(
            "Momentum execution configured | paper_mode=%s sandbox=%s",
            paper_mode,
            sandbox_mode,
        )
        data_service = DataService()
        momentum_worker = MomentumWorker(
            symbol=os.getenv("MOMENTUM_DEFAULT_SYMBOL", "PI_XBTUSD"),
            interval=os.getenv("MOMENTUM_INTERVAL", "1m"),
            execution_engine=execution_engine,
            data_service=data_service,
            momentum_period=int(os.getenv("MOMENTUM_PERIOD", "14")),
            buy_threshold=float(os.getenv("MOMENTUM_BUY_THRESHOLD", "0.01")),
            sell_threshold=float(os.getenv("MOMENTUM_SELL_THRESHOLD", "-0.01")),
            account_balance=float(os.getenv("MOMENTUM_ACCOUNT_BALANCE", "1000")),
        )
        routes_momentum.momentum_worker = momentum_worker
        routes_momentum.momentum_task = None
        routes_momentum.startup_error = None
        logger.info("Momentum worker initialized")

        try:
            await startup_safety_check(
                execution_engine=execution_engine,
                risk_manager=getattr(momentum_worker, "risk_manager", None),
                momentum_worker=momentum_worker,
                logger=logger,
            )
        except RuntimeError as exc:
            routes_momentum.startup_error = str(exc)
            logger.critical("[MAIN] STARTUP SAFETY ABORTED: %s", exc)
            setattr(momentum_worker, "enabled", False)

        can_auto_start = not bool(routes_momentum.startup_error)
        if can_auto_start and os.getenv("MOMENTUM_AUTO_START", "false").strip().lower() in {"1", "true", "yes", "on"}:
            momentum_task = asyncio.create_task(momentum_worker.start())
            routes_momentum.momentum_task = momentum_task
            logger.info("Momentum worker auto-started")
    except Exception as exc:
        momentum_worker = None
        routes_momentum.momentum_worker = None
        routes_momentum.momentum_task = None
        routes_momentum.startup_error = str(exc)
        logger.error("Failed to initialize momentum worker: %s", exc)
    
    yield
    
    # Shutdown
    logger.info("Shutting down AI Trading application...")
    if momentum_worker and momentum_worker.is_running:
        await momentum_worker.stop()
    if momentum_task and not momentum_task.done():
        momentum_task.cancel()
        with suppress(asyncio.CancelledError):
            await momentum_task
    if models:
        models = None

app = FastAPI(
    title="AI Trading API",
    description="Advanced AI-powered trading platform",
    version="1.0.0",
    lifespan=lifespan
)

RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60") or "60")
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120") or "120")
_rate_limiter: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_exempt = {"/health", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    path = request.url.path
    if path in _rate_limit_exempt:
        return await call_next(request)

    client_ip = (request.client.host if request.client else "unknown")
    key = f"{client_ip}:{path}"
    now = time.monotonic()
    bucket = _rate_limiter[key]

    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SEC:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "window_sec": RATE_LIMIT_WINDOW_SEC,
                "max_requests": RATE_LIMIT_MAX_REQUESTS,
            },
        )

    bucket.append(now)
    return await call_next(request)

# Include routers
app.include_router(routes_auth.router, prefix="/auth", tags=["auth"])
app.include_router(routes_users.router, prefix="/users", tags=["users"])
app.include_router(routes_portfolio.router, prefix="/portfolio", tags=["portfolio"])
app.include_router(routes_trade.router, prefix="/trade", tags=["trade"])
app.include_router(routes_data.router, tags=["data"])
app.include_router(routes_risk.router)
app.include_router(routes_momentum.router)
app.include_router(routes_indicators.router, prefix="/indicators", tags=["indicators"])
app.include_router(routes_strategy.router)
app.include_router(routes_backtest.router)
app.include_router(routes_webhooks.router, prefix="/api", tags=["webhooks"])


@app.post("/webhook/tradingview")
async def receive_tradingview_signal_alias(request: Request) -> dict[str, Any]:
    payload = await routes_webhooks.receive_tradingview_signal(request)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Invalid webhook response payload")
    return payload


@app.post("/api/webhook/tradingview")
async def receive_tradingview_signal_api_alias(request: Request) -> dict[str, Any]:
    payload = await routes_webhooks.receive_tradingview_signal(request)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Invalid webhook response payload")
    return payload


@app.get("/api/momentum/status")
async def momentum_status_api_alias() -> dict[str, Any]:
    payload = await routes_momentum.get_momentum_status()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Invalid momentum status payload")
    return payload


@app.get("/api/momentum/history")
async def momentum_history_api_alias(
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    payload = await routes_momentum.get_momentum_history(limit=limit)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Invalid momentum history payload")
    return payload


@app.get("/health")
async def health_check() -> dict[str, str | int | bool]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "models_loaded": models is not None,
        "active_strategies": len(strategy_manager.strategies)
    }

@app.websocket("/ws/{symbol}")
async def websocket_endpoint(websocket: WebSocket, symbol: str) -> None:
    """General WebSocket endpoint for symbol updates."""
    if str(symbol).strip().lower() == "price":
        await websocket_price_alias(websocket)
        return

    await manager.connect(websocket, symbol)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(f"{symbol}: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected from %s", symbol)
    except Exception as exc:
        logger.error("WebSocket error for %s: %s", symbol, exc)
        manager.disconnect(websocket)

@app.websocket("/ws/prices/{symbol}")
async def websocket_prices(websocket: WebSocket, symbol: str) -> None:
    """Real-time price feed with trading signals via Kraken."""
    await websocket.accept()
    logger.info("Client connected to price feed: %s", symbol)
    
    try:
        while True:
            # Fetch current price
            try:
                price = await asyncio.wait_for(kraken_broker.get_ticker(symbol), timeout=2.5)
            except Exception:
                price = 0.0

            if price <= 0:
                try:
                    price = await asyncio.wait_for(_fetch_kraken_public_price(symbol), timeout=3.0)
                except Exception:
                    price = 0.0

            if price <= 0:
                try:
                    price = await asyncio.wait_for(_fetch_kraken_spot_price(symbol), timeout=3.0)
                except Exception:
                    price = 0.0

            if price <= 0:
                try:
                    from app.services.data_service import DataService

                    fallback_symbol = "BTC"
                    symbol_upper = str(symbol or "").upper()
                    if "ETH" in symbol_upper:
                        fallback_symbol = "ETH"
                    elif "SOL" in symbol_upper:
                        fallback_symbol = "SOL"

                    fallback_price = await asyncio.wait_for(
                        DataService().get_live_price(fallback_symbol),
                        timeout=3.0,
                    )
                    price = float(getattr(fallback_price, "price", 0.0) or 0.0)
                except Exception:
                    price = 0.0
            
            if price > 0:
                # TODO: Calculate RSI from historical data
                # For now, use a placeholder RSI value
                mock_rsi = 50.0  # Replace with actual RSI calculation
                
                market_data = {
                    "price": price,
                    "rsi": mock_rsi
                }
                
                # Generate signals with all strategies
                signals = await strategy_manager.analyze_all(symbol, market_data)
                
                # Send price update with signals
                response = {
                    "type": "price",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "price": price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "signals": [
                        {
                            "type": signal.signal.value,
                            "confidence": signal.confidence,
                            "reason": signal.reason,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit
                        }
                        for signal in signals
                    ]
                }
                
                await websocket.send_json(response)
            else:
                logger.warning("Invalid price received for %s: %s", symbol, price)
            
            await asyncio.sleep(2)  # Update every 2 seconds
            
    except WebSocketDisconnect:
        logger.info("Client disconnected from price feed: %s", symbol)
    except Exception as exc:
        logger.error("Price feed error for %s: %s", symbol, exc)
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/api/v1/ws/market")
async def websocket_market_v1(websocket: WebSocket, topics: str = "market") -> None:
    """Compatibility WebSocket endpoint for v1 clients."""
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "heartbeat",
            "topic": "market",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json(
                {
                    "type": "heartbeat",
                    "topic": "market",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
    except WebSocketDisconnect:
        logger.info("Client disconnected from /api/v1/ws/market")
    except Exception as exc:
        logger.error("v1 market websocket error: %s", exc)
        with suppress(Exception):
            await websocket.close()


@app.websocket("/ws/price")
async def websocket_price_alias(websocket: WebSocket) -> None:
    """Compatibility alias for dashboard default WebSocket URL."""
    default_symbol = os.getenv("MOMENTUM_DEFAULT_SYMBOL", "PI_XBTUSD").strip() or "PI_XBTUSD"
    await websocket_prices(websocket, default_symbol)

@app.get("/test/400")
async def test_400() -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Test 400 error")

@app.get("/test/401")
async def test_401() -> None:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Test 401 error")

@app.get("/test/404")
async def test_404() -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test 404 error")

@app.get("/test/422")
async def test_422(q: int) -> dict[str, int]:
    return {"q": q}

@app.get("/test/500")
async def test_500() -> None:
    raise RuntimeError("Test 500 error")

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "path": request.url.path},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "path": request.url.path},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "path": request.url.path},
    )

@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AI Trading API", "status": "running", "docs": "/docs"}


@app.head("/")
async def root_head() -> Response:
    return Response(status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
