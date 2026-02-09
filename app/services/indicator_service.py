import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import logging

from app.schemas.indicator import IndicatorRequest, RSIResponse, MACDResponse
from app.services.data_service import get_historical_candles

logger = logging.getLogger(__name__)

async def calculate_rsi(
    db: AsyncSession,
    req: IndicatorRequest,
) -> RSIResponse:
    """Calculate RSI indicator."""
    try:
        # Fetch historical data
        candles = await get_historical_candles(db, req.symbol, days=30)
        
        if not candles or len(candles) < req.period:
            logger.warning("Insufficient data for RSI calculation: %s", req.symbol)
            return RSIResponse(
                symbol=req.symbol,
                period=req.period,
                timeframe=req.timeframe,
                rsi=50.0,  # Neutral RSI
                timestamp=datetime.now()
            )
        
        closes = np.array([float(c.get("close", 0.0)) for c in candles], dtype=float)
        
        # Calculate RSI
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:req.period])
        avg_loss = np.mean(losses[:req.period])
        
        if avg_loss == 0:
            rsi_value = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_value = 100.0 - (100.0 / (1.0 + rs))
        
        return RSIResponse(
            symbol=req.symbol,
            period=req.period,
            timeframe=req.timeframe,
            rsi=float(rsi_value),
            timestamp=datetime.now()
        )
    except Exception as exc:
        logger.error("Error calculating RSI for %s: %s", req.symbol, exc)
        # Return neutral RSI on error
        return RSIResponse(
            symbol=req.symbol,
            period=req.period,
            timeframe=req.timeframe,
            rsi=50.0,
            timestamp=datetime.now()
        )

async def calculate_macd(
    db: AsyncSession,
    req: IndicatorRequest,
) -> MACDResponse:
    """Calculate MACD indicator."""
    try:
        # Fetch historical data
        candles = await get_historical_candles(db, req.symbol, days=60)
        
        if not candles or len(candles) < 26:
            logger.warning("Insufficient data for MACD calculation: %s", req.symbol)
            return MACDResponse(
                symbol=req.symbol,
                timeframe=req.timeframe,
                period=req.period,
                macd=0.0,
                signal=0.0,
                histogram=0.0,
                timestamp=datetime.now()
            )
        
        closes = np.array([float(c.get("close", 0.0)) for c in candles], dtype=float)
        
        # Calculate EMAs
        ema_12 = _calculate_ema(closes, 12)
        ema_26 = _calculate_ema(closes, 26)
        
        macd_line = ema_12 - ema_26
        signal_line = _calculate_ema(macd_line, 9)
        histogram = macd_line[-1] - signal_line[-1]
        
        return MACDResponse(
            symbol=req.symbol,
            timeframe=req.timeframe,
            period=req.period,
            macd=float(macd_line[-1]),
            signal=float(signal_line[-1]),
            histogram=float(histogram),
            timestamp=datetime.now()
        )
    except Exception as exc:
        logger.error("Error calculating MACD for %s: %s", req.symbol, exc)
        return MACDResponse(
            symbol=req.symbol,
            timeframe=req.timeframe,
            period=req.period,
            macd=0.0,
            signal=0.0,
            histogram=0.0,
            timestamp=datetime.now()
        )

def _calculate_ema(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average."""
    multiplier = 2.0 / (period + 1)
    ema = np.zeros_like(data)
    ema[0] = data[0]
    
    for i in range(1, len(data)):
        ema[i] = (data[i] * multiplier) + (ema[i-1] * (1 - multiplier))
    
    return ema