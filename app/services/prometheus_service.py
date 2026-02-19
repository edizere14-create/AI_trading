from prometheus_client import Counter, Histogram, Gauge
import time
from functools import wraps
import logging

logger = logging.getLogger(__name__)

# Define metrics
backtest_counter = Counter(
    'backtest_runs_total',
    'Total backtest runs',
    ['symbol', 'strategy']
)

backtest_duration = Histogram(
    'backtest_duration_seconds',
    'Backtest execution time',
    ['symbol']
)

ml_signal_confidence = Gauge(
    'ml_signal_confidence',
    'Current ML signal confidence',
    ['symbol']
)

trade_counter = Counter(
    'trades_total',
    'Total trades executed',
    ['symbol', 'side', 'strategy']
)

trade_pnl = Histogram(
    'trade_pnl',
    'Trade P&L',
    ['symbol', 'strategy'],
    buckets=(-1000, -500, -100, -10, 0, 10, 100, 500, 1000, float('inf'))
)

kraken_api_latency = Histogram(
    'kraken_api_latency_seconds',
    'Kraken API response time',
    ['endpoint']
)

portfolio_value = Gauge(
    'portfolio_value_usd',
    'Current portfolio value'
)

active_grid_orders = Gauge(
    'active_grid_orders_total',
    'Number of active grid orders',
    ['symbol']
)


def track_backtest(symbol: str, strategy: str):
    """Decorator to track backtest metrics"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            backtest_counter.labels(symbol=symbol, strategy=strategy).inc()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start
                backtest_duration.labels(symbol=symbol).observe(duration)
                logger.info(f"Backtest {symbol} completed in {duration:.2f}s")
                return result
            except Exception as e:
                logger.error(f"Backtest failed: {str(e)}")
                raise
        return wrapper
    return decorator


def track_api_call(endpoint: str):
    """Decorator to track Kraken API latency"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                latency = time.time() - start
                kraken_api_latency.labels(endpoint=endpoint).observe(latency)
                return result
            except Exception as e:
                logger.error(f"API call failed: {str(e)}")
                raise
        return wrapper
    return decorator


def record_trade(symbol: str, side: str, strategy: str, pnl: float):
    """Record trade execution metrics"""
    trade_counter.labels(symbol=symbol, side=side, strategy=strategy).inc()
    trade_pnl.labels(symbol=symbol, strategy=strategy).observe(pnl)
    logger.info(f"Trade recorded: {side} {symbol} PnL: {pnl}")