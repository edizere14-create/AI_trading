from app.brokers.base import BrokerBase, OrderKind
from typing import Any, Optional
from datetime import datetime, timezone
import time
import uuid


class PaperTradingBroker(BrokerBase):
    """Paper trading broker for testing without real money."""

    def __init__(self) -> None:
        self.balance = 10000.0  # Start with $10k paper money
        self.positions: dict[str, float] = {}
        self.trades: list[dict[str, Any]] = []
        self.last_prices: dict[str, float] = {}  # <-- ensure this exists

    async def get_price(self, symbol: str) -> float:
        """Get mock price (replace with real data later)."""
        mock_prices = {
            "BTCUSD": 43500.0,
            "ETHUSD": 2300.0,
            "PI_XBTUSD": 64000.0,  # <-- add your active symbol
        }
        px = float(mock_prices.get(symbol, 60000.0))
        self.last_prices[symbol] = px
        return px

    async def get_balance(self) -> float:
        """Return current cash balance."""
        return self.balance

    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Return order status by ID."""
        for trade in self.trades:
            if trade.get("id") == order_id:
                return {
                    "status": "filled",
                    "id": trade.get("id"),
                    "order_id": trade.get("id"),
                    "filled": trade.get("qty", 0.0),
                    "filled_quantity": trade.get("qty", 0.0),
                    "avg_fill_price": trade.get("price"),
                    "price": trade.get("price"),
                    "timestamp": trade.get("timestamp"),
                    "trade": trade,
                }
        return {"status": "not_found"}

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "market",
        order_kind: OrderKind = "taker",
        expected_price: Optional[float] = None,
    ) -> dict[str, Any]:
        """Simulate order execution."""
        t0 = time.time()
        side = str(side).lower()
        current_price = await self.get_price(symbol)
        qty_float = float(quantity)

        # maker limit: if not crossing, stay open (no fill)
        if order_kind == "maker":
            if price is None:
                return {"status": "rejected", "reason": "maker_requires_price"}
            if (side == "buy" and price < current_price) or (side == "sell" and price > current_price):
                oid = f"paper_{uuid.uuid4().hex}"
                return {
                    "status": "open",
                    "id": oid,
                    "order_id": oid,
                    "filled": 0.0,
                    "filled_quantity": 0.0,
                    "avg_fill_price": None,
                    "price": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "metrics": {
                        "slippage": None,
                        "fill_rate": 0.0,
                        "latency_ms": (time.time() - t0) * 1000,
                    },
                }

        fill_price = self._resolve_fill_price(symbol, price)
        cost = qty_float * fill_price

        if side == "buy":
            if cost > self.balance:
                return {"status": "rejected", "reason": "insufficient_balance"}
            self.balance -= cost
            self.positions[symbol] = self.positions.get(symbol, 0.0) + qty_float
        else:
            if self.positions.get(symbol, 0.0) < qty_float:
                return {"status": "rejected", "reason": "insufficient_position"}
            self.balance += cost
            self.positions[symbol] -= qty_float

        trade_id = f"paper_{uuid.uuid4().hex}"
        ts = datetime.now(timezone.utc).isoformat()

        self.trades.append(
            {
                "id": trade_id,
                "symbol": symbol,
                "qty": qty_float,
                "side": side,
                "price": fill_price,
                "timestamp": ts,
            }
        )

        exp = expected_price or price or current_price
        slippage = None
        if exp:
            raw = (fill_price - exp) / exp
            slippage = raw if side == "buy" else -raw

        metrics = {
            "slippage": slippage,
            "fill_rate": 1.0,
            "latency_ms": (time.time() - t0) * 1000.0,
        }

        return {
            "status": "filled",
            "id": trade_id,
            "order_id": trade_id,
            "symbol": symbol,
            "side": side,
            "quantity": qty_float,
            "filled": qty_float,
            "filled_quantity": qty_float,
            "avg_fill_price": fill_price,
            "price": fill_price,
            "timestamp": ts,
            "metrics": metrics,
        }

    async def get_portfolio(self) -> dict[str, Any]:
        """Get current portfolio with PnL."""
        total_value = self.balance
        for symbol, qty in self.positions.items():
            px = await self.get_price(symbol)
            total_value += qty * px
        return {"balance": self.balance, "positions": self.positions, "total_value": total_value}

    def _resolve_fill_price(self, symbol: str, requested_price: float | None = None) -> float:
        if requested_price and requested_price > 0:
            return float(requested_price)
        last = self.last_prices.get(symbol)
        if last and last > 0:
            return float(last)
        return 60000.0  # fallback for paper mode