"""
Core execution engine for order routing.
Supports paper mode and live execution via CCXT (Kraken Futures demo supported).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import logging
import os
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
        sandbox: bool | None = None,
    ):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper_mode = paper_mode
        self.sandbox = self._resolve_sandbox_mode(sandbox)
        self.exchange: ccxt.Exchange | None = None
        self.max_retries = 3
        self.retry_sleep_sec = 0.5
        self.risk_manager: Any | None = None
        try:
            self.max_contracts_hard_limit = max(
                1,
                int(os.getenv("MAX_CONTRACTS_HARD_LIMIT", "5") or "5"),
            )
        except (TypeError, ValueError):
            self.max_contracts_hard_limit = 5
        try:
            self.max_leverage_ratio = max(
                0.1,
                float(os.getenv("MAX_LEVERAGE_RATIO", "5.0") or "5.0"),
            )
        except (TypeError, ValueError):
            self.max_leverage_ratio = 5.0
        try:
            self.fallback_equity = max(
                0.0,
                float(os.getenv("MOMENTUM_ACCOUNT_BALANCE", "1000") or "1000"),
            )
        except (TypeError, ValueError):
            self.fallback_equity = 1000.0

        if not self.paper_mode:
            self.exchange = self._initialize_exchange()

        logger.info(
            "ExecutionEngine initialized - paper_mode=%s exchange=%s sandbox=%s",
            self.paper_mode,
            self.exchange_id,
            self.sandbox,
        )

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _resolve_sandbox_mode(cls, explicit: bool | None) -> bool:
        if explicit is not None:
            return bool(explicit)
        raw = os.getenv("KRAKEN_SANDBOX")
        if raw is not None:
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return cls._env_bool("KRAKEN_FUTURES_DEMO", True)

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _resolve_equity_for_guard(self, signal: Dict[str, Any]) -> float | None:
        for key in ("equity", "account_equity", "account_balance"):
            val = self._to_float(signal.get(key))
            if val is not None and val > 0:
                return val

        risk_manager = getattr(self, "risk_manager", None)
        if risk_manager is not None:
            balance = self._to_float(getattr(risk_manager, "current_balance", None))
            if balance is None:
                balance = self._to_float(getattr(risk_manager, "account_balance", None))
            if balance is not None and balance > 0:
                return balance

        if self.exchange is not None and hasattr(self.exchange, "fetch_balance"):
            try:
                balance = self.exchange.fetch_balance()
                for bucket in ("total", "free"):
                    section = balance.get(bucket)
                    if isinstance(section, dict):
                        for currency in ("USD", "USDT", "ZUSD"):
                            val = self._to_float(section.get(currency))
                            if val is not None and val > 0:
                                return val
                    else:
                        val = self._to_float(section)
                        if val is not None and val > 0:
                            return val
                direct_equity = self._to_float(balance.get("equity"))
                if direct_equity is not None and direct_equity > 0:
                    return direct_equity
            except Exception as exc:
                logger.warning("Could not fetch account equity for guard checks: %s", exc)

        if self.fallback_equity > 0:
            return self.fallback_equity

        return None

    def _convert_and_validate_contracts(
        self,
        base_qty: float,
        contract_size: float,
        equity: float | None,
        mark_price: float,
        symbol: str,
        inverse: bool,
        is_exit: bool = False,
        reduce_only: bool = False,
    ) -> tuple[int, float, float, float]:
        if contract_size <= 0:
            contract_size = 1.0
        if mark_price <= 0:
            raise ValueError("mark price must be positive for contract conversion")

        if inverse:
            raw_contracts = (base_qty * mark_price) / contract_size
        else:
            raw_contracts = base_qty / contract_size
        intended_contracts = max(1, int(round(raw_contracts)))

        notional = (
            intended_contracts * contract_size
            if inverse
            else intended_contracts * contract_size * mark_price
        )
        leverage = (
            (notional / float(equity))
            if equity is not None and float(equity) > 0
            else float("inf")
        )

        # Never block risk-reducing exits; only apply hard guards to entries.
        if is_exit or reduce_only:
            logger.info(
                "[SIZE OK - EXIT] symbol=%s contracts=%s notional=%.2f leverage=%.2fx - leverage guard skipped (reduce_only exit)",
                symbol,
                intended_contracts,
                notional,
                leverage,
            )
            return intended_contracts, raw_contracts, notional, leverage

        if equity is None or equity <= 0:
            raise ValueError("missing positive equity for leverage guard")

        if intended_contracts > self.max_contracts_hard_limit:
            logger.error(
                "[HARD STOP] Contract inflation blocked: base_qty=%s contract_size=%s raw=%.6f intended=%s limit=%s symbol=%s inverse=%s",
                base_qty,
                contract_size,
                raw_contracts,
                intended_contracts,
                self.max_contracts_hard_limit,
                symbol,
                inverse,
            )
            raise ValueError(
                f"Contract size inflation guard triggered: {intended_contracts} contracts exceeds hard limit {self.max_contracts_hard_limit}"
            )

        leverage = notional / equity
        if leverage > self.max_leverage_ratio:
            logger.error(
                "[HARD STOP] Leverage guard triggered: notional=%.2f equity=%.2f leverage=%.2fx max=%.2fx symbol=%s inverse=%s",
                notional,
                equity,
                leverage,
                self.max_leverage_ratio,
                symbol,
                inverse,
            )
            raise ValueError(
                f"Leverage guard triggered: {leverage:.2f}x exceeds max allowed {self.max_leverage_ratio:.2f}x"
            )

        if raw_contracts < 0.5 and intended_contracts == 1:
            logger.warning(
                "[SIZE WARN] min(1) floor inflated size significantly: base_qty=%s raw_contracts=%.6f forced=1 notional=%.2f symbol=%s",
                base_qty,
                raw_contracts,
                notional,
                symbol,
            )

        logger.info(
            "[SIZE OK] base_qty=%s contracts=%s notional=%.2f leverage=%.2fx symbol=%s",
            base_qty,
            intended_contracts,
            notional,
            leverage,
            symbol,
        )
        return intended_contracts, raw_contracts, notional, leverage

    def _is_exit_signal(
        self,
        signal: Dict[str, Any],
        *,
        strategy_id: str,
        regime: str,
        reduce_only: bool,
    ) -> bool:
        if bool(signal.get("is_exit")):
            return True
        if reduce_only:
            return True
        return regime == "risk_exit" or strategy_id.startswith("momentum_exit")

    @staticmethod
    def _normalize_position_side(side: str) -> str:
        raw = str(side or "").strip().lower()
        if raw in {"long", "buy"}:
            return "buy"
        if raw in {"short", "sell"}:
            return "sell"
        return "buy"

    def _contracts_to_base_quantity(
        self,
        contracts: float,
        contract_size: float,
        mark_price: float,
        inverse: bool,
    ) -> float:
        qty_contracts = abs(float(contracts))
        size = max(1e-12, float(contract_size))
        px = max(0.0, float(mark_price))
        if inverse and px > 0:
            return (qty_contracts * size) / px
        return qty_contracts * size

    def get_contract_size(self, symbol: str) -> float:
        if self.exchange is None:
            return 1.0
        try:
            exchange_symbol = self._resolve_exchange_symbol(symbol)
            market = self.exchange.market(exchange_symbol)
            size = float(market.get("contractSize") or 1.0)
            return size if size > 0 else 1.0
        except Exception:
            return 1.0

    def _is_inverse_market(self, symbol: str) -> bool:
        if self.exchange is None:
            return False
        try:
            exchange_symbol = self._resolve_exchange_symbol(symbol)
            market = self.exchange.market(exchange_symbol)
            return bool(market.get("inverse"))
        except Exception:
            return False

    def get_mark_price(self, symbol: str) -> float | None:
        if self.exchange is None:
            return None
        try:
            exchange_symbol = self._resolve_exchange_symbol(symbol)
            ticker = self.exchange.fetch_ticker(exchange_symbol)
            for key in ("mark", "last", "close", "index"):
                val = self._to_float(ticker.get(key))
                if val is not None and val > 0:
                    return val
        except Exception:
            return None
        return None

    async def get_mark_price_async(self, symbol: str) -> float | None:
        return await asyncio.to_thread(self.get_mark_price, symbol)

    def get_open_positions(self) -> list[Dict[str, Any]]:
        if self.paper_mode or self.exchange is None:
            return []

        try:
            rows = self.exchange.fetch_positions()
        except Exception as exc:
            logger.warning("Could not fetch open positions: %s", exc)
            return []

        out: list[Dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            info = row.get("info") if isinstance(row.get("info"), dict) else {}
            contracts_val = self._to_float(row.get("contracts"))
            if contracts_val is None:
                contracts_val = self._to_float(info.get("size")) if isinstance(info, dict) else None
            contracts = abs(float(contracts_val or 0.0))
            if contracts <= 0:
                continue

            symbol_val = str(row.get("symbol") or row.get("id") or "").strip()
            if not symbol_val:
                continue

            side = self._normalize_position_side(str(row.get("side") or (info.get("side") if isinstance(info, dict) else "") or "buy"))
            mark_price = self._to_float(row.get("markPrice"))
            if mark_price is None and isinstance(info, dict):
                mark_price = self._to_float(info.get("markPrice"))
            if mark_price is None or mark_price <= 0:
                mark_price = self.get_mark_price(symbol_val)

            contract_size = self.get_contract_size(symbol_val)
            inverse = self._is_inverse_market(symbol_val)
            out.append(
                {
                    "symbol": symbol_val,
                    "contracts": contracts,
                    "side": side,
                    "mark_price": mark_price,
                    "contract_size": contract_size,
                    "inverse": inverse,
                }
            )
        return out

    async def get_open_positions_async(self) -> list[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_open_positions)

    def emergency_exit_position(
        self,
        *,
        symbol: str,
        current_contracts: float,
        side: str,
        mark_price: float | None,
        equity: float | None = None,
        reason: str = "emergency_exit",
        is_exit: bool = True,
    ) -> Dict[str, Any]:
        if current_contracts <= 0:
            logger.warning("[EXIT] No contracts to exit | symbol=%s", symbol)
            return {"status": "no_position", "symbol": symbol}

        exit_side = "sell" if self._normalize_position_side(side) == "buy" else "buy"
        chunk_size = max(1, int(os.getenv("EXIT_CHUNK_SIZE", "1") or "1"))
        delay_secs = max(0.0, float(os.getenv("EXIT_CHUNK_DELAY_SECS", "0.5") or "0.5"))
        remaining = int(max(1, round(float(current_contracts))))
        chunks: list[int] = []
        while remaining > 0:
            chunk = min(remaining, chunk_size)
            chunks.append(chunk)
            remaining -= chunk

        logger.warning(
            "[EXIT] Initiating | symbol=%s side=%s total=%s chunks=%s reason=%s",
            symbol,
            exit_side,
            current_contracts,
            len(chunks),
            reason,
        )

        results: list[Dict[str, Any]] = []
        for idx, chunk in enumerate(chunks, start=1):
            try:
                contract_size = self.get_contract_size(symbol)
                inverse = self._is_inverse_market(symbol)
                px = float(mark_price or 0.0) or float(self.get_mark_price(symbol) or 0.0)
                if px <= 0:
                    raise ValueError("missing mark price for emergency exit")

                base_qty = self._contracts_to_base_quantity(
                    contracts=float(chunk),
                    contract_size=contract_size,
                    mark_price=px,
                    inverse=inverse,
                )

                signal = {
                    "symbol": symbol,
                    "side": exit_side,
                    "quantity": float(base_qty),
                    "order_type": "market",
                    "order_kind": "taker",
                    "reduce_only": True,
                    "is_exit": bool(is_exit),
                    "regime": "risk_exit",
                    "strategy_id": "emergency_exit_v1",
                    "exit_reason": reason,
                    "expected_price": px,
                }
                if equity is not None:
                    signal["equity"] = float(equity)

                result = self.execute(signal)
                status = str((result or {}).get("status", "")).lower()
                if (not result) or status in {"rejected", "blocked"}:
                    raise RuntimeError((result or {}).get("error") or (result or {}).get("reason") or status or "exit_failed")

                logger.info(
                    "[EXIT] Chunk %s/%s sent | contracts=%s result_status=%s",
                    idx,
                    len(chunks),
                    chunk,
                    status or "unknown",
                )
                results.append({"chunk": idx, "contracts": chunk, "result": result})
            except Exception as exc:
                logger.error(
                    "[EXIT] Chunk %s/%s FAILED | contracts=%s error=%s",
                    idx,
                    len(chunks),
                    chunk,
                    exc,
                )
                results.append({"chunk": idx, "contracts": chunk, "error": str(exc)})

            if idx < len(chunks) and delay_secs > 0:
                time.sleep(delay_secs)

        success_chunks = [r for r in results if "error" not in r]
        failed_chunks = [r for r in results if "error" in r]
        logger.warning(
            "[EXIT] Complete | symbol=%s success=%s failed=%s",
            symbol,
            len(success_chunks),
            len(failed_chunks),
        )
        return {
            "status": "exit_attempted",
            "symbol": symbol,
            "reason": reason,
            "total_contracts": float(current_contracts),
            "success_chunks": len(success_chunks),
            "failed_chunks": len(failed_chunks),
            "chunks": results,
        }

    async def emergency_exit_position_async(self, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self.emergency_exit_position, **kwargs)

    @staticmethod
    def _blocked_result(signal: Dict[str, Any], reason: str, message: str) -> Dict[str, Any]:
        quantity = 0.0
        try:
            quantity = float(signal.get("quantity", 0.0) or 0.0)
        except Exception:
            quantity = 0.0

        return {
            "id": None,
            "status": "blocked",
            "symbol": str(signal.get("symbol", "")),
            "side": str(signal.get("side", "")).lower(),
            "quantity": quantity,
            "filled": 0.0,
            "avg_fill_price": None,
            "price": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": {"slippage": 0.0, "fill_rate": 0.0, "latency_ms": 0.0},
            "reason": reason,
            "error": message,
            "raw": {"error": message},
        }

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

    @staticmethod
    def _first_existing_market(candidates: list[str], markets: dict[str, Any]) -> str | None:
        for candidate in candidates:
            if candidate in markets:
                return candidate
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

        upper = raw.upper()
        by_id_symbol = self._market_symbol_by_id(raw)
        if by_id_symbol:
            return by_id_symbol

        static_aliases = {
            "PI_XBTUSD": ["BTC/USD:BTC", "BTC/USD:USD", "BTC/USD:USDT"],
            "PF_XBTUSD": ["BTC/USD:USD", "BTC/USD:USDT", "BTC/USD:BTC"],
            "PI_ETHUSD": ["ETH/USD:BTC", "ETH/USD:USD", "ETH/USD:USDT"],
            "PF_ETHUSD": ["ETH/USD:USD", "ETH/USD:USDT", "ETH/USD:BTC"],
            "PI_SOLUSD": ["SOL/USD:BTC", "SOL/USD:USD", "SOL/USD:USDT"],
            "PF_SOLUSD": ["SOL/USD:USD", "SOL/USD:USDT", "SOL/USD:BTC"],
            "XBTUSD": ["BTC/USD:USD", "BTC/USD:BTC", "BTC/USD:USDT"],
            "BTCUSD": ["BTC/USD:USD", "BTC/USD:BTC", "BTC/USD:USDT"],
            "ETHUSD": ["ETH/USD:USD", "ETH/USD:USDT", "ETH/USD:BTC"],
            "SOLUSD": ["SOL/USD:USD", "SOL/USD:USDT", "SOL/USD:BTC"],
        }
        alias = static_aliases.get(upper)
        if alias:
            resolved = self._first_existing_market(alias, markets)
            if resolved:
                return resolved

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
            is_exit = self._is_exit_signal(
                signal,
                strategy_id=strategy_id,
                regime=regime,
                reduce_only=reduce_only,
            )

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

                if ref_price is None:
                    ticker = self.exchange.fetch_ticker(exchange_symbol)
                    ref_price = float(ticker.get("last") or 0)
                if ref_price is None or ref_price <= 0:
                    logger.error("Could not determine reference price for contract sizing")
                    return None

                equity = self._resolve_equity_for_guard(signal)
                try:
                    contracts, _raw_contracts, notional, leverage = self._convert_and_validate_contracts(
                        base_qty=amount,
                        contract_size=contract_size,
                        equity=equity,
                        mark_price=float(ref_price),
                        symbol=exchange_symbol,
                        inverse=inverse,
                        is_exit=is_exit,
                        reduce_only=reduce_only,
                    )
                except ValueError as exc:
                    logger.error("[ORDER BLOCKED] %s", exc)
                    return self._blocked_result(signal, "size_guard_blocked", str(exc))

                if not (is_exit or reduce_only):
                    risk_manager = getattr(self, "risk_manager", None)
                    if (
                        risk_manager is not None
                        and hasattr(risk_manager, "pre_trade_notional_check")
                        and callable(getattr(risk_manager, "pre_trade_notional_check"))
                    ):
                        ok = bool(
                            risk_manager.pre_trade_notional_check(
                                contracts=contracts,
                                contract_size=contract_size,
                                mark_price=float(ref_price),
                                symbol=exchange_symbol,
                                inverse=inverse,
                            )
                        )
                        if not ok:
                            message = (
                                "risk manager blocked order: pre-trade notional/leverage check failed"
                            )
                            logger.error("[ORDER BLOCKED] %s", message)
                            return self._blocked_result(signal, "risk_manager_leverage_block", message)
                else:
                    logger.warning(
                        "Reduce-only/exit order bypassed pre-trade leverage checks | symbol=%s side=%s contracts=%s",
                        exchange_symbol,
                        side,
                        contracts,
                    )
                logger.info(
                    "Converted base quantity %s to %s contracts for %s (contract_size=%s, price=%s, inverse=%s, notional=%.2f, leverage=%.2fx)",
                    amount,
                    contracts,
                    exchange_symbol,
                    contract_size,
                    ref_price,
                    inverse,
                    notional,
                    leverage,
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
            message = str(e)
            text = message.lower()
            reason = "execution_error"
            if isinstance(e, ccxt.InsufficientFunds) or "insufficientavailablefunds" in text:
                reason = "insufficient_funds"
            elif "wouldnotreduceposition" in text:
                reason = "would_not_reduce_position"

            quantity = 0.0
            try:
                quantity = float(signal.get("quantity", 0.0) or 0.0)
            except Exception:
                quantity = 0.0

            return {
                "id": None,
                "status": OrderStatus.REJECTED.value,
                "symbol": str(signal.get("symbol", "")),
                "side": str(signal.get("side", "")).lower(),
                "quantity": quantity,
                "filled": 0.0,
                "avg_fill_price": None,
                "price": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": {"slippage": 0.0, "fill_rate": 0.0, "latency_ms": 0.0},
                "reason": reason,
                "error": message,
                "raw": {"error": message},
            }
