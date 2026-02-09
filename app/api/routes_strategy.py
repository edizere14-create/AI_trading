# FastAPI endpoints for strategies
from fastapi import APIRouter, HTTPException

from app.strategies.mean_reversion import MeanReversionStrategy
from app.services.backtest_service import BacktestService

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
