"""Order execution orchestration."""
from typing import Any, Dict, Optional, Literal, cast
import ccxt  # type: ignore
from engine.validation import validate_order_params, normalize_side, OrderSide

OrderType = Literal["market", "limit", "stop-loss-limit"]


class ExecutionManager:
    """Manages order placement and execution."""
    
    def __init__(self, exchange: ccxt.Exchange, markets: Dict[str, Any]):
        self.exchange = exchange
        self.markets = markets
        self.order_history: list[Dict[str, Any]] = []
    
    def execute_order(
        self,
        symbol: str,
        side: str,
        order_type: OrderType,
        amount: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Execute order with comprehensive error handling."""
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

            if order_type == "market":
                order = self.exchange.create_market_order(symbol, side_val, amount)
            elif order_type == "limit":
                if price is None:
                    return {"success": False, "error": "Limit orders require a price"}
                order = self.exchange.create_limit_order(symbol, side_val, amount, price)
            elif order_type == "stop-loss-limit":
                if not stop_price:
                    return {"success": False, "error": "Stop price required"}
                if price is None:
                    return {"success": False, "error": "Stop-loss limit requires a limit price"}
                order = self.exchange.create_order(
                    symbol=symbol,
                    type="limit",
                    side=side_val,
                    amount=amount,
                    price=price,
                    params={"stopPrice": stop_price}
                )
            else:
                return {"success": False, "error": f"Unknown order type: {order_type}"}

            result_dict = {
                "success": True,
                "order_id": order.get("id"),
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "type": order.get("type"),
                "amount": order.get("amount"),
                "price": order.get("price"),
                "cost": order.get("cost"),
                "status": order.get("status"),
                "timestamp": order.get("timestamp"),
                "datetime": order.get("datetime"),
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