"""
Momentum strategy worker - analyzes price momentum and generates trading signals.
Integrates with ExecutionEngine for order placement.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from engine.core.execution_engine import ExecutionEngine
from engine.core.risk_manager import RiskManager, RiskConfig
try:
    from app.strategies.momentum import MomentumStrategy
except ImportError:
    from app.strategies.momentum import RSIStrategy as MomentumStrategy
from app.services.data_service import DataService
try:
    from app.services.trade_store import trade_store
except ImportError:
    class _NullTradeStore:
        def add(self, trade: dict[str, Any]) -> None:
            return None

        def add_trade(self, trade: dict[str, Any]) -> None:
            return None

        def save_trade(self, trade: dict[str, Any]) -> None:
            return None

        def record_trade(self, trade: dict[str, Any]) -> None:
            return None

        def append(self, trade: dict[str, Any]) -> None:
            return None

    trade_store = _NullTradeStore()

logger = logging.getLogger(__name__)


class MomentumWorker:
    """
    Worker that runs Momentum strategy in a loop,
    analyzes market data, and executes orders via ExecutionEngine.
    """

    def __init__(
        self,
        symbol: str,
        interval: str = "1h",
        strategy_params: dict = None,
        execution_engine: ExecutionEngine = None,
        data_service: DataService = None,
        account_balance: float = 1000.0,
        **kwargs
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.strategy_params = strategy_params or {}

        # Log ignored args to help debugging
        if kwargs:
            logger.info("MomentumWorker received extra args: %s", list(kwargs.keys()))

        # 1. Setup Execution Engine
        if execution_engine:
            self.execution_engine = execution_engine
        else:
             # Fallback if not provided
            self.execution_engine = ExecutionEngine(
                exchange_id="krakenfutures",
                api_key=os.environ.get("KRAKEN_API_KEY", ""),
                api_secret=os.environ.get("KRAKEN_API_SECRET", ""),
                paper_mode=True, 
                sandbox=True
            )

        # 2. Setup Data Service
        if data_service:
            self.data_service = data_service
        else:
            # Fallback
            from app.services.data_service import DataService
            self.data_service = DataService(self.execution_engine)

        # 3. Strategy Configuration
        # Use kwargs if provided, else defaults
        self.momentum_period = kwargs.get("momentum_period", 14)
        self.buy_threshold = float(kwargs.get("buy_threshold", 5.0))
        self.sell_threshold = float(kwargs.get("sell_threshold", -5.0))
        self.account_balance = account_balance
        
        self.max_trades = 10  # <--- Add this line back
        
        self.strategy = MomentumStrategy(
            symbol=symbol,
            momentum_period=self.momentum_period,
            buy_threshold=self.buy_threshold,
            sell_threshold=self.sell_threshold,
        )

        risk_config = RiskConfig(
            max_position_size=0.1,
            max_drawdown=0.20,
            stop_loss_pct=0.02,
            take_profit_pct=0.05,
        )
        self.risk_manager = RiskManager(account_balance, risk_config)

        self.is_running = False
        self.last_signal: Optional[Dict[str, Any]] = None
        self.signal_history: list[dict[str, Any]] = []
        self.execution_count = 0
        self.signal_count = 0

        self.trade_count = 0
        self.trade_history: list[dict[str, Any]] = []

        # Keep recent candles/signals/trades in memory for API/dashboard
        self.candle_history: deque[dict[str, Any]] = deque(maxlen=2000)
        self.signal_history: deque[dict[str, Any]] = deque(maxlen=2000)
        self.trade_history: deque[dict[str, Any]] = deque(maxlen=2000)

    def _interval_seconds(self) -> float:
        v = self.interval
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip().lower()
            try:
                return float(s)
            except ValueError:
                pass
            if s.endswith("s"):
                return float(s[:-1])
            if s.endswith("m"):
                return float(s[:-1]) * 60.0
            if s.endswith("h"):
                return float(s[:-1]) * 3600.0
        return 60.0

    def _cache_candles(self, candles: Any) -> None:
        try:
            if candles is None:
                return

            # pandas DataFrame path
            if hasattr(candles, "iterrows"):
                for _, r in candles.iterrows():
                    ts = r.get("timestamp", datetime.now(timezone.utc))
                    self.candle_history.append(
                        {
                            "timestamp": ts,
                            "open": float(r.get("open", 0)),
                            "high": float(r.get("high", 0)),
                            "low": float(r.get("low", 0)),
                            "close": float(r.get("close", 0)),
                            "volume": float(r.get("volume", 0)),
                        }
                    )
                return

            # list/tuple path
            for row in candles:
                if isinstance(row, dict):
                    self.candle_history.append(
                        {
                            "timestamp": row.get("timestamp") or row.get("ts") or datetime.now(timezone.utc),
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0)),
                            "volume": float(row.get("volume", 0)),
                        }
                    )
                elif isinstance(row, (list, tuple)) and len(row) >= 6:
                    ts = row[0]
                    if isinstance(ts, (int, float)):
                        ts = datetime.fromtimestamp((ts / 1000) if ts > 10_000_000_000 else ts, tz=timezone.utc)
                    self.candle_history.append(
                        {
                            "timestamp": ts,
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        }
                    )
        except Exception:
            logger.exception("Failed to cache candles")

    def _persist_trade(self, trade: dict[str, Any]) -> None:
        try:
            for method_name in ("add_trade", "save_trade", "record_trade", "append"):
                fn = getattr(trade_store, method_name, None)
                if callable(fn):
                    fn(trade)
                    return
        except Exception:
            logger.exception("Failed to persist trade")

    async def start(self) -> None:
        """Start the momentum worker loop."""
        self.is_running = True
        logger.info("Starting MomentumWorker for %s", self.symbol)

        while self.is_running:
            try:
                await self._run_iteration()
                await asyncio.sleep(self._interval_seconds())
            except asyncio.CancelledError:
                self.is_running = False
                logger.info("MomentumWorker task cancelled for %s", self.symbol)
                raise
            except Exception as e:
                logger.error("Worker iteration failed: %s", e)
                await asyncio.sleep(self._interval_seconds())

    async def stop(self) -> None:
        """Stop the momentum worker."""
        self.is_running = False
        logger.info("Stopped MomentumWorker for %s", self.symbol)

    async def _run_iteration(self) -> None:
        """Run one strategy iteration with risk gate + execution."""
        try:
            logger.info("🔄 Iteration started for %s", self.symbol)

            status = self.risk_manager.get_status()
            drawdown = status.get("drawdown_pct", 0)
            if drawdown > 20.0:
                logger.critical("🛑 MAX DRAWDOWN BREACHED (%.2f%%) - STOPPING STRATEGY", drawdown)
                await self.stop()
                return

            ohlcv = await self._load_ohlcv(
                symbol=self.symbol,
                timeframe="1h",
                limit=50,
            )
            if ohlcv is None:
                logger.warning("Insufficient data for %s: 0 < %s", self.symbol, self.momentum_period)
                return

            candles = ohlcv
            self._cache_candles(candles)
            logger.info("Fetched %s candles", len(candles))
            if len(candles) < self.momentum_period:
                logger.warning(
                    "Insufficient data for %s: %s < %s",
                    self.symbol,
                    len(candles),
                    self.momentum_period,
                )
                return

            signal = await self._generate_signal(candles)
            if not signal:
                logger.info("No signal generated")
                return

            self.signal_count += 1
            self.last_signal = signal

            signal.setdefault("symbol", getattr(self, "symbol", "PI_XBTUSD"))
            signal.setdefault("strategy_id", "momentum_v1")
            signal.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            signal.setdefault("order_type", "limit")
            signal.setdefault("regime", "unknown")

            if "quantity" not in signal or float(signal.get("quantity", 0)) <= 0:
                signal["quantity"] = 0.001

            side = str(signal.get("side", "")).lower()
            symbol = str(signal.get("symbol", self.symbol))

            if side == "sell" and symbol in self.risk_manager.positions:
                allowed, reason = True, "closing position"
            else:
                allowed, reason = self.risk_manager.check_risk_limits(signal)

            if not allowed:
                logger.warning("Signal blocked by risk manager: %s | signal=%s", reason, signal)
                return

            try:
                logger.info("Executing momentum signal: %s", signal)
                result = self.execution_engine.execute(signal)
                if result:
                    self.execution_count += 1
                    self.trade_count += 1

                    order_record, trade_record = self._build_order_record(signal, result)
                    self.signal_history.append(order_record)
                    if trade_record:
                        self.trade_history.append(trade_record)
                        self._persist_trade(trade_record)

                    if self.max_trades and self.trade_count >= self.max_trades:
                        logger.info("Reached max trades (%s). Stopping worker.", self.max_trades)
                        await self.stop()
                        return

                    logger.info("Order placed: %s", result.get("id", result))
                else:
                    order_record, trade_record = self._build_order_record(signal, None)
                    self.signal_history.append(order_record)
                    logger.warning("Execution returned no result")
            except Exception:
                logger.exception("Order execution failed")

        except Exception as e:
            logger.error("Iteration failed: %s", e, exc_info=True)

    async def _load_ohlcv(self, symbol: str, timeframe: str, limit: int):
        """Load OHLCV candles with compatibility fallbacks across DataService versions."""
        data_service = self.data_service

        get_ohlcv_fn = getattr(data_service, "get_ohlcv", None)
        if callable(get_ohlcv_fn):
            return await get_ohlcv_fn(symbol=symbol, timeframe=timeframe, limit=limit)

        fetch_ohlcv_fn = getattr(data_service, "fetch_ohlcv", None)
        if callable(fetch_ohlcv_fn):
            result = fetch_ohlcv_fn(symbol=symbol, timeframe=timeframe, limit=limit)
            if inspect.isawaitable(result):
                return await result
            return result

        logger.warning("DataService has no get_ohlcv/fetch_ohlcv; using ccxt fallback for %s", symbol)
        return await self._load_ohlcv_via_ccxt(symbol=symbol, timeframe=timeframe, limit=limit)

    async def _load_ohlcv_via_ccxt(self, symbol: str, timeframe: str, limit: int):
        """Fallback OHLCV fetch using ccxt directly."""
        try:
            import ccxt
            import pandas as pd
        except Exception as exc:
            raise RuntimeError(f"OHLCV fallback unavailable (missing deps): {exc}")

        def _to_ccxt_symbol(raw_symbol: str) -> str:
            raw = (raw_symbol or "").strip().upper()
            mapping = {
                "PI_XBTUSD": "BTC/USD:USD",
                "PF_XBTUSD": "BTC/USD:USD",
                "PI_ETHUSD": "ETH/USD:USD",
                "PF_ETHUSD": "ETH/USD:USD",
                "PI_SOLUSD": "SOL/USD:USD",
                "PF_SOLUSD": "SOL/USD:USD",
            }
            if raw in mapping:
                return mapping[raw]
            return raw_symbol

        def _fetch() -> "pd.DataFrame":
            exchange = ccxt.krakenfutures(
                {
                    "apiKey": os.environ.get("KRAKEN_API_KEY", ""),
                    "secret": os.environ.get("KRAKEN_API_SECRET", ""),
                    "enableRateLimit": True,
                    "timeout": 30000,
                }
            )
            demo = os.environ.get("KRAKEN_FUTURES_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"}
            exchange.set_sandbox_mode(demo)

            ccxt_symbol = _to_ccxt_symbol(symbol)
            rows = exchange.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, limit=int(limit))
            df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df

        return await asyncio.to_thread(_fetch)

    async def _generate_signal(self, df) -> dict[str, Any] | None:
        """Generate a normalized signal dict or None."""
        if df is None or getattr(df, "empty", True):
            logger.warning("No market data returned for %s", self.symbol)
            return None

        if hasattr(self.strategy, "analyze"):
            generated = self.strategy.analyze(df)
        else:
            sig = inspect.signature(self.strategy.generate_signal)
            if len(sig.parameters) == 0:
                generated = self.strategy.generate_signal()
            else:
                generated = self.strategy.generate_signal(df)

        if inspect.isawaitable(generated):
            generated = await generated

        if not generated:
            return None

        signal: dict[str, Any] = dict(generated)
        signal["symbol"] = self.symbol
        signal["strategy_id"] = signal.get("strategy_id", "momentum_v1")
        signal["timestamp"] = datetime.now(timezone.utc).isoformat()
        signal["order_type"] = signal.get("order_type", "limit")
        signal["action"] = signal.get("action", signal.get("side", "buy"))
        signal["side"] = signal.get("side", signal["action"])

        if "price" in signal:
            signal["price"] = float(signal["price"])
        if "quantity" in signal:
            signal["quantity"] = float(signal["quantity"])

        return signal

    def _extract_fee(self, result: Dict[str, Any] | None) -> float | None:
        if not result:
            return 0.0 if getattr(self.execution_engine, "paper_mode", False) else None
        raw = result.get("raw") or {}
        fee = raw.get("fee")
        if isinstance(fee, dict) and fee.get("cost") is not None:
            return float(fee["cost"])
        fees = raw.get("fees")
        if isinstance(fees, list):
            total = 0.0
            for f in fees:
                if isinstance(f, dict) and f.get("cost") is not None:
                    total += float(f["cost"])
            return total
        return 0.0 if getattr(self.execution_engine, "paper_mode", False) else None

    def _build_order_record(
        self,
        signal: Dict[str, Any],
        result: Dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        side = str(signal.get("side", "")).lower()
        symbol = str(signal.get("symbol", self.symbol))
        regime = signal.get("regime", "unknown")

        status = (result or {}).get("status", "rejected")
        avg_fill_price = float((result or {}).get("avg_fill_price") or (result or {}).get("price") or 0.0)
        filled = float((result or {}).get("filled") or 0.0)
        slippage = float(((result or {}).get("metrics") or {}).get("slippage") or 0.0)
        fees = self._extract_fee(result)

        entry_price = None
        exit_price = None
        pnl = None
        outcome = "rejected"
        trade_record = None

        if status in ("filled", "partial") and avg_fill_price > 0:
            if symbol in self.risk_manager.positions:
                pos = self.risk_manager.positions.get(symbol) or {}
                pos_side = str(pos.get("side", "buy")).lower()

                # Close: opposite side of position
                if (pos_side == "buy" and side == "sell") or (pos_side == "sell" and side == "buy"):
                    entry_price = float(pos.get("entry_price", 0.0))
                    size = float(signal.get("quantity", 0))
                    exit_price = avg_fill_price
                    
                    if pos_side == "buy":
                        pnl = (exit_price - entry_price) * size
                    else:
                        pnl = (entry_price - exit_price) * size

                    outcome = "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")

                    self.risk_manager.close_position(symbol, exit_price)

                    trade_record = {
                        "timestamp": (result or {}).get("timestamp") or signal.get("timestamp"),
                        "symbol": symbol,
                        "side": "long" if pos_side == "buy" else "short",
                        "size": size,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "fees": fees,
                        "slippage": slippage,
                        "regime": regime,
                        "outcome": outcome,
                    }
            else:
                # Open new position
                self.risk_manager.open_position(symbol, side, signal.get("quantity", 0), avg_fill_price)
                entry_price = avg_fill_price
                outcome = "open"

        order_record = {
            "symbol": symbol,
            "side": side,
            "quantity": float(signal.get("quantity", 0)),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "fees": fees,
            "slippage": slippage,
            "regime": regime,
            "outcome": outcome,
            "pnl": pnl,
            "status": status,
            "filled": filled,
            "avg_fill_price": avg_fill_price,  # ← ENSURE THIS
            "order_id": (result or {}).get("id"),
            "timestamp": (result or {}).get("timestamp") or signal.get("timestamp"),
        }

        return order_record, trade_record

    def get_analytics(self) -> dict[str, Any]:
        trades = [t for t in self.trade_history if t.get("pnl") is not None]
        pnls = [float(t["pnl"]) for t in trades]
        total_trades = len(pnls)

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_trades = len(wins)
        loss_trades = len(losses)
        win_rate = (win_trades / total_trades) * 100 if total_trades else 0.0
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        best_trade = max(pnls) if pnls else 0.0
        worst_trade = min(pnls) if pnls else 0.0
        session_pnl = sum(pnls) if pnls else 0.0

        return {
            "total_trades": total_trades,
            "win_trades": win_trades,
            "loss_trades": loss_trades,
            "win_rate": round(win_rate, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "best_trade": round(best_trade, 4),
            "worst_trade": round(worst_trade, 4),
            "session_pnl": round(session_pnl, 4),
            "trades": trades,
        }

    def get_status(self) -> Dict[str, Any]:
        """Return worker status with risk metrics."""
        base_status = {
            "symbol": self.symbol,
            "is_running": self.is_running,
            "last_signal": self.last_signal,
            "signal_count": self.signal_count,
            "execution_count": self.execution_count,
            "interval": self.interval,
        }
        base_status["risk"] = self.risk_manager.get_status()
        return base_status

    async def force_close(self) -> dict[str, Any]:
        """Force-close any open position for testing round-trip PnL."""
        if not self.risk_manager.positions:
            return {"status": "no_positions"}

        results: list[dict[str, Any]] = []

        for symbol, pos in list(self.risk_manager.positions.items()):
            side = str(pos.get("side", "buy")).lower()
            close_side = "sell" if side == "buy" else "buy"
            qty = float(pos.get("size") or pos.get("quantity") or 0.0)
            if qty <= 0:
                continue

            signal = {
                "symbol": symbol,
                "side": close_side,
                "quantity": qty,
                "order_type": "market",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "strategy_id": "manual_close",
            }

            result = self.execution_engine.execute(signal)
            order_record, trade_record = self._build_order_record(signal, result)
            self.signal_history.append(order_record)

            if trade_record:
                self.trade_history.append(trade_record)
                self._persist_trade(trade_record)

            results.append({"symbol": symbol, "result": result})

        return {"status": "closed", "results": results}

    async def force_open(self, side: str = "buy", quantity: float = 0.001) -> dict[str, Any]:
        """Force-open a test position (market order)."""
        side = "buy" if str(side).lower() not in ("buy", "sell") else str(side).lower()
        signal = {
            "symbol": self.symbol,
            "side": side,
            "quantity": float(quantity),
            "order_type": "market",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy_id": "manual_open",
        }
        result = self.execution_engine.execute(signal)
        order_record, trade_record = self._build_order_record(signal, result)
        self.signal_history.append(order_record)

        if trade_record:
            self.trade_history.append(trade_record)
            self._persist_trade(trade_record)

        return {"status": "opened", "result": result}

    async def initialize(self):
        """Initialize the worker."""
        # Pass data_service to paper executor
        if hasattr(self.execution_engine, 'executor'):
            if hasattr(self.execution_engine.executor, 'data_service'):
                self.execution_engine.executor.data_service = self.data_service