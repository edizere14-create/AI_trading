from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncGenerator
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.websocket_service import ConnectionManager
from app.strategy_manager import StrategyManager
from app.brokers.kraken import KrakenBroker
from app.utils.ai_models import TradingAIModels
from app.api import routes_auth, routes_users, routes_portfolio, routes_trade, routes_data, routes_risk, routes_indicators, routes_strategy, routes_backtest, routes_webhooks, routes_momentum

logger = logging.getLogger(__name__)

models: TradingAIModels | None = None
manager: ConnectionManager = ConnectionManager()
strategy_manager: StrategyManager = StrategyManager()
momentum_worker: Any | None = None
momentum_task: asyncio.Task | None = None

# Initialize Kraken broker
kraken_broker = KrakenBroker(
    api_key=os.getenv("KRAKEN_API_KEY", ""),
    api_secret=os.getenv("KRAKEN_API_SECRET", "")
)

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

        execution_engine = ExecutionEngine(
            exchange_id="krakenfutures",
            api_key=os.getenv("KRAKEN_API_KEY", ""),
            api_secret=os.getenv("KRAKEN_API_SECRET", ""),
            paper_mode=True,
            sandbox=True,
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

        if os.getenv("MOMENTUM_AUTO_START", "false").strip().lower() in {"1", "true", "yes", "on"}:
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

# Include routers
app.include_router(routes_auth.router, prefix="/auth", tags=["auth"])
app.include_router(routes_users.router, prefix="/users", tags=["users"])
app.include_router(routes_portfolio.router, prefix="/portfolio", tags=["portfolio"])
app.include_router(routes_trade.router, prefix="/trade", tags=["trade"])
app.include_router(routes_data.router, prefix="/data", tags=["data"])
app.include_router(routes_risk.router)
app.include_router(routes_momentum.router)
app.include_router(routes_indicators.router, prefix="/indicators", tags=["indicators"])
app.include_router(routes_strategy.router, prefix="/strategy", tags=["strategy"])
app.include_router(routes_backtest.router, prefix="/backtest", tags=["backtest"])
app.include_router(routes_webhooks.router, prefix="/api", tags=["webhooks"])

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
            price = await kraken_broker.get_ticker(symbol)
            
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
            
            await asyncio.sleep(5)  # Update every 5 seconds
            
    except WebSocketDisconnect:
        logger.info("Client disconnected from price feed: %s", symbol)
    except Exception as exc:
        logger.error("Price feed error for %s: %s", symbol, exc)
        try:
            await websocket.close()
        except Exception:
            pass

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
