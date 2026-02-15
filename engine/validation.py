from typing import Any, Dict, Optional, cast, Literal
import ccxt  # type: ignore

OrderSide = Literal["buy", "sell"]

def normalize_side(side: str) -> OrderSide:
    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side: {side}. Must be 'buy' or 'sell'")
    return cast(OrderSide, side)


def validate_order_params(
    exchange: ccxt.Exchange,
    markets: Dict[str, Any],
    symbol: str,
    side: str,
    amount: float,
    price: Optional[float] = None
) -> tuple[bool, Any]:
    """Validate order parameters against exchange limits."""
    try:
        _ = normalize_side(side)
        market = markets[symbol]

        min_amount = market["limits"]["amount"]["min"]
        max_amount = market["limits"]["amount"]["max"]
        min_cost = market["limits"]["cost"]["min"]

        amount_str = exchange.amount_to_precision(symbol, amount)
        amount = float(amount_str) if amount_str is not None else amount

        if amount < min_amount:
            return False, f"Amount too small. Minimum: {min_amount}"
        if max_amount and amount > max_amount:
            return False, f"Amount too large. Maximum: {max_amount}"

        validated_price: Optional[float] = None
        if price is not None:
            price_str = exchange.price_to_precision(symbol, price)
            validated_price = float(price_str) if price_str is not None else price

            cost = amount * validated_price
            if cost < min_cost:
                return False, f"Order value too small. Minimum: ${min_cost}"

        return True, {"amount": amount, "price": validated_price}

    except KeyError as e:
        return False, f"Market data missing for {symbol}: {str(e)}"
    except Exception as e:
        return False, f"Validation error: {str(e)}"