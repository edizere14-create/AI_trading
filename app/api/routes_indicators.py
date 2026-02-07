from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.indicator import IndicatorRequest, RSIResponse, MACDResponse
from app.services.indicator_service import calculate_rsi, calculate_macd
from app.utils.dependencies import get_db_dep

router = APIRouter()

@router.post("/rsi", response_model=RSIResponse)
async def calculate_rsi_endpoint(
    req: IndicatorRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> RSIResponse:
    """Calculate RSI (Relative Strength Index) for symbol."""
    try:
        return await calculate_rsi(db, req)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to calculate RSI",
        )

@router.post("/macd", response_model=MACDResponse)
async def calculate_macd_endpoint(
    req: IndicatorRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> MACDResponse:
    """Calculate MACD (Moving Average Convergence Divergence) for symbol."""
    try:
        return await calculate_macd(db, req)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to calculate MACD",
        )