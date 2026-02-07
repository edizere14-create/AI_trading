# FastAPI endpoints for strategies
from fastapi import APIRouter, HTTPException


from app.strategies.momentum import MomentumStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.services.backtest_service import BacktestService
from fastapi import APIRouter, HTTPException

from app.strategies.momentum import MomentumStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.services.backtest_service import BacktestService
@router.post("/strategies/backtest")
async def backtest_strategy(strategy_name: str, data: list[dict]):
	if strategy_name == "momentum":
		strategy = MomentumStrategy()
	elif strategy_name == "mean_reversion":
		strategy = MeanReversionStrategy()
	else:
		raise HTTPException(status_code=400, detail="Unknown strategy.")
	service = BacktestService(strategy)
	signals = service.run(data)
	return {"signals": signals}

router = APIRouter()

@router.get("/strategies")
async def list_strategies():
    return ["momentum", "mean_reversion"]


@router.post("/strategies/momentum/signal")
async def run_momentum_strategy(data: list[dict]):
	strategy = MomentumStrategy()
	signal = strategy.generate_signals(data)
	if signal is None:
		raise HTTPException(status_code=400, detail="Not enough data for signal generation.")
	return signal

@router.post("/strategies/mean_reversion/signal")
async def run_mean_reversion_strategy(data: list[dict]):
	strategy = MeanReversionStrategy()
	signal = strategy.generate_signals(data)
	if signal is None:
		raise HTTPException(status_code=400, detail="Not enough data for signal generation.")
	return signal
