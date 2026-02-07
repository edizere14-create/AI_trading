from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import numpy as np

from app.schemas.indicator import IndicatorRequest, RSIResponse, MACDResponse
from app.services.data_service import get_historical_candles

async def calculate_rsi(
    db: AsyncSession,
    req: IndicatorRequest,
) -> RSIResponse:
    """Calculate RSI indicator."""
    # Fetch historical data
    candles = await get_historical_candles(req.symbol, days=30)
    
    if not candles or len(candles) < req.period:
        raise ValueError(f"Not enough data for RSI calculation. Need at least {req.period} candles")
    
    closes = np.array([c.get("close", 0.0) for c in candles], dtype=float)
    rsi = calculate_rsi_values(closes, req.period)
    
    return RSIResponse(
        symbol=req.symbol,
        timeframe=req.timeframe,
        rsi=float(rsi[-1]),
        period=req.period,
        timestamp=datetime.utcnow(),
    )

async def calculate_macd(
    db: AsyncSession,
    req: IndicatorRequest,
) -> MACDResponse:
    """Calculate MACD indicator."""
    # Fetch historical data
    candles = await get_historical_candles(req.symbol, days=30)
    
    if not candles or len(candles) < 26:
        raise ValueError("Not enough data for MACD calculation. Need at least 26 candles")
    
    closes = np.array([c.get("close", 0.0) for c in candles], dtype=float)
    macd_line, signal_line, histogram = calculate_macd_values(closes)
    
    return MACDResponse(
        symbol=req.symbol,
        timeframe=req.timeframe,
        macd=float(macd_line[-1]),
        signal=float(signal_line[-1]),
        histogram=float(histogram[-1]),
        period=req.period,
        timestamp=datetime.utcnow(),
    )

def calculate_rsi_values(closes: np.ndarray, period: int) -> np.ndarray:
    """Calculate RSI values from close prices."""
    deltas = np.diff(closes)
    seed = deltas[:period + 1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    
    rsi = np.zeros_like(closes)
    rsi[:period] = 100.0 - 100.0 / (1.0 + rs)
    
    for i in range(period, len(closes)):
        delta = deltas[i - 1]
        if delta > 0:
            up_val = delta
            down_val = 0.0
        else:
            up_val = 0.0
            down_val = -delta
        
        up = (up * (period - 1) + up_val) / period
        down = (down * (period - 1) + down_val) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    
    return rsi

def calculate_macd_values(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate MACD, Signal, and Histogram."""
    ema_12 = ema(closes, 12)
    ema_26 = ema(closes, 26)
    macd_line = ema_12 - ema_26
    signal_line = ema(macd_line, 9)
    histogram = macd_line - signal_line
    
    return macd_line, signal_line, histogram

def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average."""
    return data.ewm(span=period, adjust=False).mean()