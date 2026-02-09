from app.brokers.base import BrokerBase
from abc import ABC, abstractmethod
from typing import Any, Optional

class PaperTradingBroker(BrokerBase):
    """Paper trading broker for testing without real money."""
    
    def __init__(self) -> None:
        self.balance = 10000.0  # Start with $10k paper money
        self.positions: dict[str, float] = {}
        self.trades: list[dict[str, Any]] = []
    
    async def get_price(self, symbol: str) -> float:
        """Get mock price (replace with real data later)."""
        mock_prices = {
            "BTCUSD": 43500.0,
            "ETHUSD": 2300.0,
        }
        return mock_prices.get(symbol, 0.0)
    
    async def get_balance(self) -> float:
        """Return current cash balance."""
        return self.balance
    
    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Return order status by ID."""
        for trade in self.trades:
            if trade.get("id") == order_id:
                return {"status": "filled", "trade": trade}
        return {"status": "not_found"}
    
    async def place_order(self, symbol: str, qty: float, side: str, price: Optional[float] = None) -> dict[str, Any]:
        """Simulate order execution."""
        current_price = await self.get_price(symbol)
        qty_float = qty
        cost = qty_float * current_price
        
        if side == "buy":
            if cost > self.balance:
                return {"status": "rejected", "reason": "insufficient_balance"}
            self.balance -= cost
            self.positions[symbol] = self.positions.get(symbol, 0.0) + qty_float
        else:
            if symbol not in self.positions or self.positions[symbol] < qty_float:
                return {"status": "rejected", "reason": "insufficient_position"}
            self.balance += cost
            self.positions[symbol] -= qty_float
        
        self.trades.append({
            "symbol": symbol,
            "qty": qty_float,
            "side": side,
            "price": current_price,
            "timestamp": "now"
        })
        return {"status": "filled", "price": current_price}
    
    async def get_portfolio(self) -> dict[str, Any]:
        """Get current portfolio with PnL."""
        total_value = self.balance
        for symbol, qty in self.positions.items():
            price = await self.get_price(symbol)
            total_value += qty * price
        return {"balance": self.balance, "positions": self.positions, "total_value": total_value}