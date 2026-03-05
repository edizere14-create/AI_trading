"""Strategy management endpoints."""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any
import logging

# import backtrader as bt  # TODO: lazy load - causes startup issues
from app.schemas.strategy import StrategySignalResponse
from app.services.data_service import DataService
from app.services.strategy_service import StrategyEngine, StrategyService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strategies", tags=["strategies"])

@router.post("/run")
async def run_strategy(strategy_code: str, symbol: str, timeframe: str) -> dict[str, str]:
    """Run live strategy (not backtest)."""
    if strategy_code == "momentum":
        # Implement momentum logic
        return {
            "status": "running",
            "strategy": strategy_code,
            "symbol": symbol,
            "timeframe": timeframe,
            "message": "Momentum strategy started"
        }
    raise HTTPException(status_code=400, detail="Unknown strategy")


@router.get(
    "/signal",
    response_model=StrategySignalResponse,
    summary="Generate strategy signal",
    description="Runs multi-timeframe signal generation with mode-based aggressiveness adjustment.",
    responses={
        200: {
            "description": "Structured strategy signal",
            "content": {
                "application/json": {
                    "example": {
                        "symbol": "PI_XBTUSD",
                        "mode": "balanced",
                        "action": "buy",
                        "confidence": 0.72,
                        "aggressiveness": 0.94,
                        "suggested_size_pct": 0.0135,
                        "multi_timeframe": [
                            {
                                "timeframe": "1h",
                                "momentum": 1.21,
                                "trend_strength": 0.84,
                                "volatility": 18.7,
                                "score": 11.9,
                            },
                            {
                                "timeframe": "4h",
                                "momentum": 2.15,
                                "trend_strength": 1.42,
                                "volatility": 15.3,
                                "score": 17.1,
                            },
                        ],
                        "metadata": {
                            "aggregate_score": 14.5,
                            "average_volatility": 17.0,
                        },
                    }
                }
            },
        }
    },
)
async def generate_strategy_signal(
    symbol: str = Query(default="PI_XBTUSD", description="Market symbol to analyze", examples=["PI_XBTUSD"]),
    mode: str = Query(default="balanced", description="Aggressiveness mode: conservative|balanced|aggressive", examples=["balanced"]),
    timeframes: str = Query(default="15m,1h,4h", description="Comma-separated timeframes", examples=["15m,1h,4h"]),
    lookback: int = Query(default=300, ge=60, le=2000, description="Candles per timeframe", examples=[300]),
) -> StrategySignalResponse:
    try:
        requested_timeframes = [t.strip() for t in timeframes.split(",") if t.strip()]
        if not requested_timeframes:
            requested_timeframes = ["1h"]

        data_service = DataService()
        frames = {}
        for tf in requested_timeframes:
            frames[tf] = await data_service.get_ohlcv(symbol=symbol, timeframe=tf, limit=lookback)

        engine = StrategyEngine()
        return engine.generate_signal(symbol=symbol, frames=frames, mode=mode)
    except Exception as exc:
        logger.exception("Signal generation failed")
        raise HTTPException(status_code=500, detail=f"Signal generation failed: {exc}") from exc
