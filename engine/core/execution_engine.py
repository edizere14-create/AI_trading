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
        extra_params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if post_only:
            params["postOnly"] = True
        if extra_params:
            params.update(extra_params)
        return self.exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side,
            amount=amount,
            price=price if order_type == "limit" else None,
            params=params,
        )

    @staticmethod
    def _is_post_only_rejection(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "postwouldexecute" in text or "orderimmediatelyfillable" in text

    @staticmethod
    def _is_would_not_reduce_position(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "wouldnotreduceposition" in text

    def _repriced_maker_limit(
        self,
        symbol: str,
        side: str,
        current_price: float | None,
        step_bps: float = 3.0,
    ) -> float | None:
        if self.exchange is None:
            return current_price

        try:
            ticker = self.exchange.fetch_ticker(symbol)
        except Exception as exc:
            logger.warning("Failed to fetch ticker for maker reprice (%s): %s", symbol, exc)
            return current_price

        bid = float(ticker.get("bid") or 0.0)
        ask = float(ticker.get("ask") or 0.0)
        last = float(ticker.get("last") or 0.0)
        offset = max(0.0001, float(step_bps) / 10_000.0)

        if side == "buy":
            reference = bid if bid > 0 else (last if last > 0 else current_price or 0.0)
            if reference <= 0:
                return current_price
            candidate = reference * (1.0 - offset)
            if current_price is not None:
                candidate = min(candidate, current_price * (1.0 - offset))
        else:
            reference = ask if ask > 0 else (last if last > 0 else current_price or 0.0)
            if reference <= 0:
                return current_price
            candidate = reference * (1.0 + offset)
            if current_price is not None:
                candidate = max(candidate, current_price * (1.0 + offset))

        try:
            return float(self.exchange.price_to_precision(symbol, candidate))
        except Exception:
            return float(candidate)

    def _fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_order(order_id, symbol)

    def _market_symbol_by_id(self, market_id: str) -> str | None:
        if self.exchange is None:
            return None
        markets_by_id = getattr(self.exchange, "markets_by_id", None) or {}
        target = str(market_id or "").strip().upper()
        if not target:
            return None
        for key, market in markets_by_id.items():
            if str(key).strip().upper() != target:
                continue
            if isinstance(market, list):
                market = market[0] if market else None
            if isinstance(market, dict):
                symbol = market.get("symbol")
                if symbol:
                    return str(symbol)
        return None

    def _resolve_exchange_symbol(self, symbol: str) -> str:
        if self.exchange is None:
            return symbol

        raw = str(symbol or "").strip()
        if not raw:
            return symbol

        markets = getattr(self.exchange, "markets", None) or {}
        if raw in markets:
            return raw

        by_id_symbol = self._market_symbol_by_id(raw)
        if by_id_symbol:
            return by_id_symbol

        upper = raw.upper()
        static_aliases = {
            "PI_XBTUSD": "BTC/USD:USD",
            "PF_XBTUSD": "BTC/USD:USD",
            "PI_ETHUSD": "ETH/USD:USD",
            "PF_ETHUSD": "ETH/USD:USD",
            "PI_SOLUSD": "SOL/USD:USD",
            "PF_SOLUSD": "SOL/USD:USD",
            "XBTUSD": "BTC/USD:USD",
            "BTCUSD": "BTC/USD:USD",
            "ETHUSD": "ETH/USD:USD",
            "SOLUSD": "SOL/USD:USD",
        }
        alias = static_aliases.get(upper)
        if alias and alias in markets:
            return alias

        if upper.startswith("PI_"):
            by_id_symbol = self._market_symbol_by_id(upper.replace("PI_", "PF_", 1))
            if by_id_symbol:
                return by_id_symbol
        if upper.startswith("PF_"):
            by_id_symbol = self._market_symbol_by_id(upper.replace("PF_", "PI_", 1))
            if by_id_symbol:
                return by_id_symbol

        raise ValueError(f"Unknown exchange symbol: {symbol}")

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
            strategy_id = str(signal.get("strategy_id", "")).lower()
            regime = str(signal.get("regime", "")).lower()
            reduce_only = bool(signal.get("reduce_only") or signal.get("reduceOnly"))
            if regime == "risk_exit" or strategy_id.startswith("momentum_exit"):
                reduce_only = True

            order_params: Dict[str, Any] = {}
            provided_params = signal.get("params")
            if isinstance(provided_params, dict):
                order_params.update(provided_params)
            if reduce_only:
                order_params["reduceOnly"] = True

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

            exchange_symbol = self._resolve_exchange_symbol(symbol)
            if exchange_symbol != symbol:
                logger.info("Resolved execution symbol %s -> %s", symbol, exchange_symbol)

            if order_type == "limit" and price is None:
                logger.error("Limit (maker) order requires price")
                return None

            market = self.exchange.market(exchange_symbol)

            if order_type == "limit" and price is not None:
                try:
                    price = float(self.exchange.price_to_precision(exchange_symbol, price))
                except Exception as exc:
                    logger.warning("Could not normalize limit price precision for %s: %s", exchange_symbol, exc)

            # Futures exchanges (e.g., krakenfutures) expect contract units for amount.
            if market.get("contract"):
                contract_size = float(market.get("contractSize") or 1.0)
                if contract_size <= 0:
                    contract_size = 1.0
                inverse = bool(market.get("inverse"))
                ref_price = price

                if inverse and ref_price is None:
                    ticker = self.exchange.fetch_ticker(exchange_symbol)
                    ref_price = float(ticker.get("last") or 0)
                if inverse and (ref_price is None or ref_price <= 0):
                    logger.error("Could not determine reference price for inverse-contract sizing")
                    return None

                if inverse:
                    contracts = max(1, int(round((amount * float(ref_price)) / contract_size)))
                else:
                    contracts = max(1, int(round(amount / contract_size)))
                logger.info(
                    "Converted base quantity %s to %s contracts for %s (contract_size=%s, price=%s, inverse=%s)",
                    amount,
                    contracts,
                    exchange_symbol,
                    contract_size,
                    ref_price,
                    inverse,
                )
                amount = float(contracts)

            amount = float(self.exchange.amount_to_precision(exchange_symbol, amount))
            if amount <= 0:
                logger.error("Order amount rounded to zero for %s", exchange_symbol)
                return None

            if expected_price is None:
                try:
                    ticker = self.exchange.fetch_ticker(exchange_symbol)
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

                try:
                    order = self._submit_order(
                        symbol=exchange_symbol,
                        side=side,
                        amount=remaining,
                        order_type=order_type,
                        price=price,
                        post_only=(order_kind == "maker"),
                        extra_params=order_params,
                    )
                except Exception as exc:
                    if order_kind == "maker" and self._is_post_only_rejection(exc):
                        repriced = self._repriced_maker_limit(
                            symbol=exchange_symbol,
                            side=side,
                            current_price=price,
                        )
                        logger.warning(
                            "Maker order rejected as immediately fillable (attempt %s/%s): %s | old_price=%s new_price=%s",
                            attempt,
                            self.max_retries,
                            exc,
                            price,
                            repriced,
                        )
                        if repriced is not None and (price is None or abs(repriced - price) > 1e-12):
                            price = repriced
                            time.sleep(self.retry_sleep_sec)
                            continue

                        final_status = OrderStatus.REJECTED.value
                        last_order = {
                            "id": None,
                            "status": final_status,
                            "reason": "post_only_would_execute",
                            "error": str(exc),
                        }
                        break
                    if reduce_only and self._is_would_not_reduce_position(exc):
                        logger.warning(
                            "Reduce-only order would not reduce position; treating as no-op | symbol=%s side=%s amount=%s",
                            exchange_symbol,
                            side,
                            remaining,
                        )
                        final_status = OrderStatus.CANCELLED.value
                        last_order = {
                            "id": None,
                            "status": final_status,
                            "reason": "would_not_reduce_position",
                            "error": str(exc),
                        }
                        break
                    raise
                last_order = order

                logger.info(
                    "LIVE ORDER (attempt %s/%s): %s %s %s %s id=%s",
                    attempt,
                    self.max_retries,
                    side.upper(),
                    remaining,
                    exchange_symbol,
                    order_type.upper(),
                    order.get("id"),
                )

                time.sleep(self.retry_sleep_sec)

                try:
                    fetched = self._fetch_order(order["id"], exchange_symbol)
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

                if order_kind == "maker":
                    # Keep post-only maker orders resting on book so they remain visible in open orders.
                    if status in ("open", "new", "pending", "submitted"):
                        final_status = OrderStatus.PARTIAL.value if total_filled > 0 else OrderStatus.SUBMITTED.value
                        break
                    if status in ("canceled", "cancelled", "rejected", "expired"):
                        final_status = OrderStatus.CANCELLED.value
                        break
                    if status in ("closed", "filled"):
                        final_status = (
                            OrderStatus.FILLED.value if total_filled >= amount else OrderStatus.PARTIAL.value
                        )
                        break
                    final_status = OrderStatus.PARTIAL.value if total_filled > 0 else OrderStatus.SUBMITTED.value
                    break

                if status in ("canceled", "rejected", "expired"):
                    final_status = OrderStatus.CANCELLED.value
                    continue

                # Partial or open: cancel and retry remainder
                if remaining > 0:
                    try:
                        self.exchange.cancel_order(fetched["id"], exchange_symbol)
                    except Exception as exc:
                        logger.warning("Cancel failed for %s: %s", fetched.get("id"), exc)

            end_ts = time.time()
            avg_fill_price = (total_cost / total_filled) if total_filled > 0 else None
            if total_filled > 0 and total_filled < amount:
                final_status = OrderStatus.PARTIAL.value
            elif total_filled <= 0 and final_status not in (
                OrderStatus.SUBMITTED.value,
                OrderStatus.CANCELLED.value,
            ):
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

            result = {
                "id": last_order.get("id") if last_order else None,
                "status": final_status,
                "symbol": symbol,
                "exchange_symbol": exchange_symbol,
                "side": side,
                "quantity": amount,
                "filled": total_filled,
                "avg_fill_price": avg_fill_price,
                "price": avg_fill_price,
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
