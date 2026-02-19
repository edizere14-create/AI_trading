from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import pandas as pd
import logging

from app.services.backtest_service import BacktestService
from app.services.ml_signal_service import MLSignalService
from app.services.data_service import DataService
from app.schemas.backtest import BacktestRequest, BacktestResponse
from app.db.database import get_db  # Import your DB dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backtest", tags=["Backtesting"])

@router.post("/run", response_model=BacktestResponse)
async def run_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)  # Use Depends() for injection
):
    """Run ML-enhanced backtest"""
    try:
        # Get market data
        data_service = DataService()
        data = await data_service.get_historical_data(
            request.symbol,
            request.start_date,
            request.end_date,
            '1d'
        )
        
        # Generate ML signals
        ml_service = MLSignalService()
        signals = ml_service.generate_signals(data)
        
        # Run backtest
        backtest_service = BacktestService(db)
        metrics = backtest_service.run_backtest(
            request.symbol,
            request.start_date,
            request.end_date,
            data,
            signals,
            request.initial_capital
        )
        
        # Save to database
        background_tasks.add_task(
            _save_backtest_results,
            db,
            request.symbol,
            metrics
        )
        
        return BacktestResponse(**metrics)
        
    except Exception as e:
        logger.error(f"Backtest failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


async def _save_backtest_results(db: Session, symbol: str, metrics: dict):
    """Background task to save results"""
    from app.db.models import BacktestResult
    
    result = BacktestResult(
        symbol=symbol,
        strategy_name="ML-Enhanced",
        start_date=metrics['start_date'],
        end_date=metrics['end_date'],
        initial_capital=metrics.get('initial_capital', 10000),
        final_value=metrics['final_value'],
        total_return=metrics['total_return'],
        sharpe_ratio=metrics['sharpe_ratio'],
        max_drawdown=metrics['max_drawdown'],
        total_trades=metrics['total_trades'],
        win_rate=metrics['win_rate'],
        profit_factor=metrics['profit_factor'],
    )
    
    db.add(result)
    db.commit()