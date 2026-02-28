"""
Core execution engine for order routing.
Supports paper mode and live execution via CCXT (Kraken Futures demo supported).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import logging
import time
import asyncio
from typing import Any, Dict, Tuple

import ccxt

logger = logging.getLogger(__name__)


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ExecutionEngine:
    """Executes trading signals via exchange API or paper mode."""

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        paper_mode: bool = True,
        sandbox: bool = False,
    ):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper_mode = paper_mode
        self.sandbox = sandbox
        self.exchange: ccxt.Exchange | None = None
        self.max_retries = 3
        self.retry_sleep_sec = 0.5

        if not self.paper_mode:
            self.exchange = self._initialize_exchange()

        logger.info(
            "ExecutionEngine initialized - paper_mode=%s exchange=%s sandbox=%s",
            self.paper_mode,
            self.exchange_id,
            self.sandbox,
        )

    def _initialize_exchange(self) -> ccxt.Exchange:
        """Initialize CCXT exchange client."""
        if not self.api_key or not self.api_secret:
            raise ValueError("API credentials are required when paper_mode=False")

        exchange_class = getattr(ccxt, self.exchange_id)
        exchange = exchange_class(
            {
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "enableRateLimit": True,
                "timeout": 30000,
            }
        )

        if self.sandbox:
            try:
                exchange.set_sandbox_mode(True)
            except Exception as exc:
                logger.warning("Sandbox mode not supported for %s: %s", self.exchange_id, exc)

        try:
            exchange.load_markets()
        except Exception as exc:
            logger.warning("Could not preload markets for %s: %s", self.exchange_id, exc)

        return exchange

    def _compute_metrics(
        self,
        side: str,
        expected_price: float | None,
        avg_fill_price: float | None,
        filled: float,
        amount: float,
        latency_ms: float,
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
        side: str,
        amount: float,
        order_type: str,
        price: float | None,
        post_only: bool,
    ) -> Dict[str, Any]:
        params = {"postOnly": True} if post_only else {}
        return self.exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side,
            amount=amount,
            price=price if order_type == "limit" else None,
            params=params,
        )

    def _fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_order(order_id, symbol)

    async def execute_signal(self, signal: Dict[str, Any]) -> Dict[str, Any] | None:
        """Async wrapper for execute()."""
        return await asyncio.to_thread(self.execute, signal)

    def execute(self, signal: Dict[str, Any]) -> Dict[str, Any] | None:
        """Execute trading signal."""
        try:
            symbol = str(signal["symbol"])
            side = str(signal["side"]).lower()
            amount = float(signal["quantity"])
            order_kind = str(signal.get("order_kind", "taker")).lower()  # maker | taker
            order_type = "limit" if order_kind == "maker" else "market"
            price = signal.get("price")
            price = float(price) if price is not None else None
            expected_price = signal.get("expected_price")
            expected_price = float(expected_price) if expected_price is not None else None

            if self.paper_mode:
                # Always resolve a concrete paper fill
                trade_price = float(
                    price or expected_price or signal.get("price") or 60000.0
                )

                logger.info(
                    "PAPER TRADE: %s %s %s @ %s",
                    side.upper(),
                    amount,
                    symbol,
                    trade_price,
                )

                return {
                    "id": f"paper_{symbol}_{side}_{int(time.time())}",
                    "status": "filled",
                    "symbol": symbol,
                    "side": side,
                    "quantity": float(amount),  # FIX: amount, not quantity
                    "filled": float(amount),  # FIX: amount, not quantity
                    "avg_fill_price": trade_price,
                    "price": trade_price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "metrics": {
                        "slippage": 0.0,
                        "fill_rate": 1.0,
                        "latency_ms": 0.0,
                    },
                }

            if self.exchange is None:
                logger.error("Live execution requested but exchange client is not initialized")
                return None

            if order_type == "limit" and price is None:
                logger.error("Limit (maker) order requires price")
                return None

            market = self.exchange.market(symbol)

            # Futures exchanges (e.g., krakenfutures) expect contract units for amount.
            if market.get("contract"):
                contract_size = float(market.get("contractSize") or 1.0)
                ref_price = price
                if ref_price is None:
                    ticker = self.exchange.fetch_ticker(symbol)
                    ref_price = float(ticker.get("last") or 0)
                if ref_price <= 0:
                    logger.error("Could not determine reference price for contract sizing")
                    return None

                contracts = max(1, int(round((amount * ref_price) / contract_size)))
                logger.info(
                    "Converted base quantity %s to %s contracts for %s (contract_size=%s, price=%s)",
                    amount,
                    contracts,
                    symbol,
                    contract_size,
                    ref_price,
                )
                amount = float(contracts)

            amount = float(self.exchange.amount_to_precision(symbol, amount))
            if amount <= 0:
                logger.error("Order amount rounded to zero for %s", symbol)
                return None

            if expected_price is None:
                try:
                    ticker = self.exchange.fetch_ticker(symbol)
                    expected_price = float(ticker.get("last") or 0) or None
                except Exception:
                    expected_price = price

            remaining = amount
            total_filled = 0.0
            total_cost = 0.0
            final_status = OrderStatus.SUBMITTED.value
            last_order = None
            start_ts = time.time()

            for attempt in range(1, self.max_retries + 1):
                if remaining <= 0:
                    break

                order = self._submit_order(
                    symbol=symbol,
                    side=side,
                    amount=remaining,
                    order_type=order_type,
                    price=price,
                    post_only=(order_kind == "maker"),
                )
                last_order = order

                logger.info(
                    "LIVE ORDER (attempt %s/%s): %s %s %s %s id=%s",
                    attempt,
                    self.max_retries,
                    side.upper(),
                    remaining,
                    symbol,
                    order_type.upper(),
                    order.get("id"),
                )

                time.sleep(self.retry_sleep_sec)

                try:
                    fetched = self._fetch_order(order["id"], symbol)
                except Exception as exc:
                    logger.warning("fetch_order not supported; using create_order response: %s", exc)
                    fetched = order

                filled = float(fetched.get("filled") or 0.0)
                cost = float(fetched.get("cost") or 0.0)
                status = str(fetched.get("status") or "").lower()

                total_filled += filled
                total_cost += cost
                remaining = max(0.0, remaining - filled)
                last_order = fetched

                if total_filled >= amount:
                    final_status = OrderStatus.FILLED.value
                    break

                if status in ("canceled", "rejected", "expired"):
                    final_status = OrderStatus.CANCELLED.value
                    continue

                # Partial or open: cancel and retry remainder
                if remaining > 0:
                    try:
                        self.exchange.cancel_order(fetched["id"], symbol)
                    except Exception as exc:
                        logger.warning("Cancel failed for %s: %s", fetched.get("id"), exc)

            end_ts = time.time()
            avg_fill_price = (total_cost / total_filled) if total_filled > 0 else None
            if total_filled > 0 and total_filled < amount:
                final_status = OrderStatus.PARTIAL.value
            elif total_filled <= 0:
                final_status = OrderStatus.REJECTED.value

            metrics = self._compute_metrics(
                side=side,
                expected_price=expected_price or price,
                avg_fill_price=avg_fill_price,
                filled=total_filled,
                amount=amount,
                latency_ms=(end_ts - start_ts) * 1000.0,
            )

            logger.info(
                "EXECUTION METRICS: slippage=%s fill_rate=%.3f latency_ms=%.1f",
                metrics.get("slippage"),
                metrics.get("fill_rate"),
                metrics.get("latency_ms"),
            )

            # before returning result dict
            resolved_fill = avg_fill_price or price or expected_price or signal.get("price")
            if resolved_fill is not None:
                result["avg_fill_price"] = float(resolved_fill)
                result["price"] = float(resolved_fill)

            result = {
                "id": last_order.get("id") if last_order else None,
                "status": final_status,
                "symbol": symbol,
                "side": side,
                "quantity": amount,
                "filled": total_filled,
                "avg_fill_price": avg_fill_price,
                "price": avg_fill_price,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": metrics,
                "raw": last_order,
            }

            # Normalize ONLY after result exists
            resolved_fill = avg_fill_price or price or expected_price or signal.get("price")
            if resolved_fill is not None:
                result["avg_fill_price"] = float(resolved_fill)
                result["price"] = float(resolved_fill)

            if result.get("metrics") is None:
                result["metrics"] = {}
            result["metrics"]["slippage"] = result["metrics"].get("slippage", 0.0)
            result["metrics"]["latency_ms"] = result["metrics"].get("latency_ms", 0.0)

            return result
        except Exception as e:
            logger.error("Execution failed: %s", e, exc_info=True)
            return None
