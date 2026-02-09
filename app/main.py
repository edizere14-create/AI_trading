from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

from app.services.websocket_service import ConnectionManager
from app.strategy_manager import StrategyManager
from app.brokers.kraken import KrakenBroker
from app.utils.ai_models import TradingAIModels
from app.api import routes_auth, routes_users, routes_portfolio, routes_trade, routes_data, routes_risk, routes_indicators, routes_strategy

logger = logging.getLogger(__name__)

models: TradingAIModels | None = None
manager: ConnectionManager = ConnectionManager()
strategy_manager: StrategyManager = StrategyManager()

# Initialize Kraken broker
kraken_broker = KrakenBroker(
    api_key=os.getenv("KRAKEN_API_KEY", ""),
    api_secret=os.getenv("KRAKEN_API_SECRET", "")
)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup/shutdown."""
    global models
    
    # Startup
    logger.info("Starting AI Trading application...")
    try:
        models = TradingAIModels()
        logger.info("AI models initialized")
        
        # Initialize strategies
        strategy_manager.create_rsi_strategy("XXBTZUSD", overbought=70, oversold=30)
        strategy_manager.create_rsi_strategy("XETHZUSD", overbought=70, oversold=30)
        logger.info("Trading strategies initialized")
    except Exception as exc:
        logger.error("Failed to initialize application: %s", exc)
    
    yield
    
    # Shutdown
    logger.info("Shutting down AI Trading application...")
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
app.include_router(routes_risk.router, prefix="/risk", tags=["risk"])
app.include_router(routes_indicators.router, prefix="/indicators", tags=["indicators"])
app.include_router(routes_strategy.router, prefix="/strategy", tags=["strategy"])

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
