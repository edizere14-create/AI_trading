from fastapi import APIRouter
router = APIRouter()

from . import routes_auth, routes_data, routes_strategy
from . import routes_portfolio, routes_risk, routes_trade, routes_indicators

__all__ = [
    "routes_auth", "routes_data", "routes_strategy",
    "routes_portfolio", "routes_risk", "routes_trade", "routes_indicators"
]
