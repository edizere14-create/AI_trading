from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

import ccxt
from dotenv import load_dotenv


@dataclass(frozen=True)
class SmokeConfig:
    symbol: str | None
    amount: float
    price_multiplier: float


def _build_exchange() -> ccxt.Exchange:
    load_dotenv()
    api_key = os.getenv("KRAKEN_API_KEY", "").strip()
    api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise RuntimeError("Missing KRAKEN_API_KEY or KRAKEN_API_SECRET in environment/.env")

    exchange = ccxt.krakenfutures(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
        }
    )
    exchange.set_sandbox_mode(True)
    return exchange


def _resolve_symbol(exchange: ccxt.Exchange, requested: str | None) -> str:
    markets = exchange.load_markets()
    if requested:
        if requested not in markets:
            raise RuntimeError(f"Requested symbol '{requested}' not found in Kraken Futures demo markets")
        return requested

    preferred = "BTC/USD:USD"
    if preferred in markets:
        return preferred

    candidates = [
        symbol
        for symbol, market in markets.items()
        if market.get("active") and str(symbol).endswith("/USD:USD")
    ]
    if not candidates:
        raise RuntimeError("No active USD perpetual-like market found for smoke test")
    return candidates[0]


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception as exc:  # pragma: no cover - defensive conversion
        raise RuntimeError(f"Unable to convert value to float: {value}") from exc


def run_smoke(config: SmokeConfig) -> int:
    exchange = _build_exchange()
    symbol = _resolve_symbol(exchange, config.symbol)

    ticker = exchange.fetch_ticker(symbol)
    last = _to_float(ticker.get("last"))
    if last <= 0:
        raise RuntimeError(f"Invalid last price for {symbol}: {last}")

    price = round(last * config.price_multiplier, 1)
    if price <= 0:
        raise RuntimeError(f"Computed test order price is invalid: {price}")

    print(f"SMOKE_SYMBOL={symbol}")
    print(f"SMOKE_LAST={last}")
    print(f"SMOKE_PRICE={price}")
    print(f"SMOKE_AMOUNT={config.amount}")

    order = exchange.create_order(
        symbol=symbol,
        type="limit",
        side="buy",
        amount=config.amount,
        price=price,
        params={"postOnly": True},
    )

    order_id = str(order.get("id") or "")
    if not order_id:
        raise RuntimeError("Exchange returned order without id")

    print(f"SMOKE_ORDER_CREATED={order_id}")
    print(f"SMOKE_CREATE_STATUS={order.get('status')}")

    cancel = exchange.cancel_order(order_id, symbol)
    print(f"SMOKE_CANCELLED={cancel.get('id') or order_id}")
    print(f"SMOKE_CANCEL_STATUS={cancel.get('status')}")

    open_orders = exchange.fetch_open_orders(symbol)
    print(f"SMOKE_OPEN_ORDERS_AFTER_CANCEL={len(open_orders)}")

    if any(str(o.get("id")) == order_id for o in open_orders):
        raise RuntimeError("Smoke order still appears in open orders after cancel")

    print("DEMO_SMOKE_RESULT=OK")
    return 0


def parse_args() -> SmokeConfig:
    parser = argparse.ArgumentParser(description="Kraken Futures demo safe smoke test")
    parser.add_argument("--symbol", default=None, help="Market symbol, e.g. BTC/USD:USD")
    parser.add_argument("--amount", type=float, default=1.0, help="Contract amount (default: 1)")
    parser.add_argument(
        "--price-multiplier",
        type=float,
        default=0.5,
        help="Limit price multiplier on last price (default: 0.5 to avoid fills)",
    )

    args = parser.parse_args()
    if args.amount <= 0:
        raise ValueError("--amount must be > 0")
    if args.price_multiplier <= 0:
        raise ValueError("--price-multiplier must be > 0")

    return SmokeConfig(
        symbol=args.symbol,
        amount=float(args.amount),
        price_multiplier=float(args.price_multiplier),
    )


def main() -> int:
    try:
        config = parse_args()
        return run_smoke(config)
    except Exception as exc:
        print(f"DEMO_SMOKE_RESULT=FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
