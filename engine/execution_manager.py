"""Order execution orchestration."""
from typing import Any, Dict, Optional, Literal, cast
import time
import logging
import ccxt  # type: ignore
from engine.validation import validate_order_params, normalize_side, OrderSide

logger = logging.getLogger(__name__)

OrderType = Literal["market", "limit", "stop-loss-limit"]
OrderKind = Literal["maker", "taker"]


class ExecutionManager:
    """Manages order placement and execution."""
    
    def __init__(self, exchange: ccxt.Exchange, markets: Dict[str, Any]):
        self.exchange = exchange
        self.markets = markets
        self.order_history: list[Dict[str, Any]] = []
        self.max_retries = 3
        self.retry_sleep_sec = 0.5

    def _compute_metrics(
        self,
        side: str,
        expected_price: Optional[float],
        avg_fill_price: Optional[float],
        filled: float,
        amount: float,
        latency_ms: float
    ) -> Dict[str, Any]:
        fill_rate = (filled / amount) if amount > 0 else 0.0
        slippage = None
        if expected_price and avg_fill_price:
            raw = (avg_fill_price - expected_price) / expected_price
            slippage = raw if side == "buy" else -raw
        return {
            "slippage": slippage,
            "fill_rate": fill_rate,
            "latency_ms": latency_ms,
        }

    def _submit_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        price: Optional[float],
        post_only: bool,
        stop_price: Optional[float] = None
    ) -> Dict[str, Any]:
        if order_type == "market":
            return self.exchange.create_market_order(symbol, side, amount)
        if order_type == "limit":
            params = {"postOnly": True} if post_only else {}
            return self.exchange.create_limit_order(symbol, side, amount, price, params=params)
        if order_type == "stop-loss-limit":
            return self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=amount,
                price=price,
                params={"stopPrice": stop_price}
            )
        raise ValueError(f"Unknown order type: {order_type}")

    def _fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_order(order_id, symbol)

    def execute_order(
        self,
        symbol: str,
        side: str,
        order_type: OrderType,
        amount: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        order_kind: OrderKind = "taker",
        expected_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Execute order with retry, partial fill handling, and metrics."""
        try:
            is_valid, result = validate_order_params(
                self.exchange,
                self.markets,
                symbol,
                side,
                amount,
                price
            )
            if not is_valid:
                return {"success": False, "error": result}

            side_val: OrderSide = normalize_side(side)
            amount = result["amount"]
            price = result["price"]

            # Maker/Taker handling (only for market/limit types)
            post_only = False
            if order_type in ("market", "limit"):
                if order_kind == "maker":
                    order_type = "limit"
                    post_only = True
                    if price is None:
                        return {"success": False, "error": "Maker (limit) orders require a price"}
                else:
                    order_type = "market"

            remaining = float(amount)
            total_filled = 0.0
            total_cost = 0.0
            last_order: Optional[Dict[str, Any]] = None
            start_ts = time.time()

            for attempt in range(1, self.max_retries + 1):
                if remaining <= 0:
                    break

                order = self._submit_order(
                    symbol=symbol,
                    side=side_val,
                    order_type=order_type,
                    amount=remaining,
                    price=price,
                    post_only=post_only,
                    stop_price=stop_price
                )
                last_order = order

                time.sleep(self.retry_sleep_sec)
                fetched = self._fetch_order(order["id"], symbol)

                filled = float(fetched.get("filled") or 0.0)
                cost = float(fetched.get("cost") or 0.0)
                status = str(fetched.get("status") or "").lower()

                total_filled += filled
                total_cost += cost
                remaining = max(0.0, amount - total_filled)
                last_order = fetched

                if total_filled >= amount:
                    break

                if status in ("canceled", "rejected", "expired"):
                    continue

                # Partial/open: cancel and retry remaining
                if remaining > 0:
                    try:
                        self.exchange.cancel_order(fetched["id"], symbol)
                    except Exception as exc:
                        logger.warning("Cancel failed for %s: %s", fetched.get("id"), exc)

            end_ts = time.time()
            avg_fill_price = (total_cost / total_filled) if total_filled > 0 else None
            if total_filled >= amount:
                final_status = "filled"
            elif total_filled > 0:
                final_status = "partial"
            else:
                final_status = "rejected"

            metrics = self._compute_metrics(
                side=side,
                expected_price=expected_price or price,
                avg_fill_price=avg_fill_price,
                filled=total_filled,
                amount=amount,
                latency_ms=(end_ts - start_ts) * 1000.0
            )

            result_dict = {
                "success": True,
                "order_id": last_order.get("id") if last_order else None,
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "amount": amount,
                "price": price,
                "filled": total_filled,
                "avg_fill_price": avg_fill_price,
                "status": final_status,
                "timestamp": last_order.get("timestamp") if last_order else None,
                "datetime": last_order.get("datetime") if last_order else None,
                "metrics": metrics,
                "raw": last_order,
            }

            self.order_history.append(result_dict)
            return result_dict

        except ccxt.InsufficientFunds as e:
            return {"success": False, "error": f"Insufficient funds: {str(e)}"}
        except ccxt.InvalidOrder as e:
            return {"success": False, "error": f"Invalid order: {str(e)}"}
        except ccxt.OrderNotFound as e:
            return {"success": False, "error": f"Order not found: {str(e)}"}
        except ccxt.NetworkError as e:
            return {"success": False, "error": f"Network error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    def place_stop_loss_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        limit_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Place a stop-loss order."""
        try:
            side_val = normalize_side(side)
            opposite_side: OrderSide = cast(OrderSide, "sell" if side_val == "buy" else "buy")

            if not limit_price:
                limit_price = stop_price

            is_valid, result = validate_order_params(
                self.exchange,
                self.markets,
                symbol,
                opposite_side,
                amount,
                limit_price
            )
            if not is_valid:
                return {"success": False, "error": result}

            amount = result["amount"]
            limit_price = result["price"]

            order = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=opposite_side,
                amount=amount,
                price=limit_price,
                params={"stopPrice": stop_price}
            )

            result_dict = {
                "success": True,
                "order_id": order.get("id"),
                "type": "stop-loss",
                "stop_price": stop_price,
                "limit_price": limit_price,
                "amount": amount,
            }
            self.order_history.append(result_dict)
            return result_dict

        except Exception as e:
            return {"success": False, "error": f"Stop-loss failed: {str(e)}"}

    def place_take_profit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        take_profit_price: float
    ) -> Dict[str, Any]:
        """Place a take-profit order."""
        try:
            side_val = normalize_side(side)
            opposite_side: OrderSide = cast(OrderSide, "sell" if side_val == "buy" else "buy")

            is_valid, result = validate_order_params(
                self.exchange,
                self.markets,
                symbol,
                opposite_side,
                amount,
                take_profit_price
            )
            if not is_valid:
                return {"success": False, "error": result}

            amount = result["amount"]
            take_profit_price = result["price"]

            order = self.exchange.create_limit_order(
                symbol,
                opposite_side,
                amount,
                take_profit_price
            )

            result_dict = {
                "success": True,
                "order_id": order.get("id"),
                "type": "take-profit",
                "price": take_profit_price,
                "amount": amount,
            }
            self.order_history.append(result_dict)
            return result_dict

        except Exception as e:
            return {"success": False, "error": f"Take-profit failed: {str(e)}"}

    def get_order_history(self) -> list[Dict[str, Any]]:
        """Get all executed orders."""
        return self.order_history