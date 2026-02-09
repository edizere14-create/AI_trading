from typing import Any, cast
from fastapi import APIRouter
from app.brokers.paper_trading import PaperTradingBroker

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# Initialize broker
broker: PaperTradingBroker = PaperTradingBroker()

@router.get("/")
async def get_portfolio() -> Any:
    """Get current portfolio with PnL."""
    portfolio = await broker.get_portfolio()
    return portfolio