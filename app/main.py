from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from app.core.config import settings
from app.utils.dependencies import get_db
from app.api import (
    routes_auth, routes_users, routes_data, routes_strategy, 
    routes_portfolio, routes_risk, routes_trade, routes_indicators
)
from app.services.websocket_service import manager
from app.utils.ai_models import TradingAIModels

models = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global models
    models = TradingAIModels()
    yield
    models = None

app = FastAPI(
    title="AI Trading API", 
    version="0.1.0", 
    lifespan=lifespan
)

# Auth & Users
app.include_router(routes_auth.router, prefix="/auth", tags=["auth"])
app.include_router(routes_users.router, prefix="/users", tags=["users"])

# Market Data
app.include_router(routes_data.router, prefix="/data", tags=["data"])

# Trading & Portfolio
app.include_router(routes_trade.router, prefix="/trade", tags=["trade"])
app.include_router(routes_portfolio.router, prefix="/portfolio", tags=["portfolio"])

# Risk & Indicators
app.include_router(routes_risk.router, prefix="/risk", tags=["risk"])
app.include_router(routes_indicators.router, prefix="/indicators", tags=["indicators"])

# Strategy
app.include_router(routes_strategy.router, prefix="/strategy", tags=["strategy"])

@app.websocket("/ws/{symbol}")
async def websocket_endpoint(websocket: WebSocket, symbol: str):
    await manager.connect(websocket, symbol)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(f"{symbol}: {data}")
    except:
        manager.disconnect(websocket)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "ai_models": models is not None}
