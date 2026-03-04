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

import pandas as pd
from engine.backtest_engine import BacktestEngine
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
            paper_mode = self._env_bool("TRADING_PAPER_MODE", True)
            sandbox_mode = self._env_bool("KRAKEN_FUTURES_DEMO", True)
            self.execution_engine = ExecutionEngine(
                exchange_id="krakenfutures",
                api_key=os.environ.get("KRAKEN_API_KEY", ""),
                api_secret=os.environ.get("KRAKEN_API_SECRET", ""),
                paper_mode=paper_mode,
                sandbox=sandbox_mode,
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
        
        try:
            self.max_trades = int(os.environ.get("MOMENTUM_MAX_TRADES", "0") or "0")
        except (TypeError, ValueError):
            self.max_trades = 0
        if self.max_trades < 0:
            self.max_trades = 0
        self.live_maker_only = self._env_bool("MOMENTUM_LIVE_MAKER_ONLY", True)
        try:
            self.live_maker_offset_bps = float(os.environ.get("MOMENTUM_LIVE_MAKER_OFFSET_BPS", "8") or "8")
        except (TypeError, ValueError):
            self.live_maker_offset_bps = 8.0
        self.live_maker_offset_bps = max(0.0, min(self.live_maker_offset_bps, 500.0))
        self.live_taker_high_confidence = self._env_bool("MOMENTUM_LIVE_TAKER_ON_HIGH_CONFIDENCE", True)
        try:
            self.live_taker_confidence_threshold = float(
                os.environ.get("MOMENTUM_LIVE_TAKER_CONFIDENCE_THRESHOLD", "90.0") or "90.0"
            )
        except (TypeError, ValueError):
            self.live_taker_confidence_threshold = 90.0
        self.live_taker_confidence_threshold = max(0.0, min(self.live_taker_confidence_threshold, 100.0))
        self.enforce_execution_gates = self._env_bool("MOMENTUM_ENFORCE_EXECUTION_GATES", True)
        try:
            self.entry_confidence_gate_pct = float(os.environ.get("MOMENTUM_ENTRY_CONF_GATE_PCT", "55.0") or "55.0")
        except (TypeError, ValueError):
            self.entry_confidence_gate_pct = 55.0
        self.entry_confidence_gate_pct = max(0.0, min(self.entry_confidence_gate_pct, 100.0))
        try:
            self.entry_conviction_gate = float(os.environ.get("MOMENTUM_ENTRY_CONVICTION_GATE", "0.35") or "0.35")
        except (TypeError, ValueError):
            self.entry_conviction_gate = 0.35
        self.entry_conviction_gate = max(0.0, min(self.entry_conviction_gate, 1.0))
        try:
            self.entry_agreement_gate = float(os.environ.get("MOMENTUM_ENTRY_AGREEMENT_GATE", "0.30") or "0.30")
        except (TypeError, ValueError):
            self.entry_agreement_gate = 0.30
        self.entry_agreement_gate = max(0.0, min(self.entry_agreement_gate, 1.0))
        self.sync_exchange_state = self._env_bool("MOMENTUM_SYNC_EXCHANGE_STATE", True)
        self.cancel_stale_orders = self._env_bool("MOMENTUM_CANCEL_STALE_ORDERS", True)
        try:
            self.cancel_stale_after_sec = float(os.environ.get("MOMENTUM_CANCEL_STALE_SEC", "300") or "300")
        except (TypeError, ValueError):
            self.cancel_stale_after_sec = 300.0
        self.cancel_stale_after_sec = max(30.0, self.cancel_stale_after_sec)
        try:
            self.max_stale_cancels_per_iter = int(os.environ.get("MOMENTUM_CANCEL_STALE_MAX", "20") or "20")
        except (TypeError, ValueError):
            self.max_stale_cancels_per_iter = 20
        self.max_stale_cancels_per_iter = max(1, self.max_stale_cancels_per_iter)
        self.exit_max_loss_pct = float(os.environ.get("MOMENTUM_EXIT_MAX_LOSS_PCT", "0.02") or "0.02")
        self.exit_time_stop_bars = int(os.environ.get("MOMENTUM_EXIT_TIME_STOP_BARS", "20") or "20")
        self.exit_volatility_contraction = float(
            os.environ.get("MOMENTUM_EXIT_VOL_CONTRACTION_FACTOR", "0.60") or "0.60"
        )
        self.exit_liquidity_vacuum_factor = float(
            os.environ.get("MOMENTUM_EXIT_LIQUIDITY_VACUUM_FACTOR", "0.35") or "0.35"
        )
        self.exit_correlation_spike_abs = float(
            os.environ.get("MOMENTUM_EXIT_CORRELATION_SPIKE_ABS", "0.85") or "0.85"
        )

        self.live_auto_train_enabled = self._env_bool("MOMENTUM_AUTO_TRAIN_ENABLED", True)
        self.auto_train_every_n_iters = int(os.environ.get("MOMENTUM_AUTO_TRAIN_EVERY", "30") or "30")
        self.auto_train_lookback_rows = int(os.environ.get("MOMENTUM_AUTO_TRAIN_LOOKBACK", "300") or "300")
        self.auto_train_min_rows = int(os.environ.get("MOMENTUM_AUTO_TRAIN_MIN_ROWS", "120") or "120")
        self.auto_train_every_n_iters = max(1, self.auto_train_every_n_iters)
        self.auto_train_lookback_rows = max(80, self.auto_train_lookback_rows)
        self.auto_train_min_rows = max(80, self.auto_train_min_rows)
        self.auto_train_period_grid = self._parse_int_csv_env(
            "MOMENTUM_AUTO_TRAIN_PERIOD_GRID",
            [8, 12, 14, 20, 26],
        )
        self.auto_train_buy_grid = self._parse_float_csv_env(
            "MOMENTUM_AUTO_TRAIN_BUY_GRID",
            [0.8, 1.0, 1.2, 1.6, 2.0],
        )
        self.auto_train_sell_grid = [
            -abs(v)
            for v in self._parse_float_csv_env(
                "MOMENTUM_AUTO_TRAIN_SELL_GRID",
                [0.8, 1.0, 1.2, 1.6, 2.0],
            )
        ]
        self.auto_train_period_grid = [max(3, int(v)) for v in self.auto_train_period_grid]
        self.auto_train_buy_grid = [max(0.1, float(v)) for v in self.auto_train_buy_grid]
        self.auto_train_sell_grid = [min(-0.1, float(v)) for v in self.auto_train_sell_grid]
        self._auto_train_iter_count = 0
        self._auto_train_last_iter = -self.auto_train_every_n_iters
        self._last_context_metrics: dict[str, Any] = {}
        self.position_guards: dict[str, dict[str, Any]] = {}
        
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
        self.last_decision_reason = "initialized"

        # Keep recent candles/signals/trades in memory for API/dashboard
        self.candle_history: deque[dict[str, Any]] = deque(maxlen=2000)
        self.signal_history: deque[dict[str, Any]] = deque(maxlen=2000)
        self.trade_history: deque[dict[str, Any]] = deque(maxlen=2000)
        self.open_orders_snapshot: list[dict[str, Any]] = []
        self.exchange_position_cache: dict[str, dict[str, Any]] = {}
        self.last_open_orders_fetch_ok = False
        self.last_positions_fetch_ok = False
        self.last_entry_gate_snapshot: dict[str, Any] = {}

        self.live_train_engine: BacktestEngine | None = None
        if self.live_auto_train_enabled:
            self.live_train_engine = BacktestEngine(
                strategy_fn=self._momentum_strategy_for_backtest,
                initial_balance=float(account_balance),
                fee_rate=0.0006,
                slippage_pct=0.0005,
                spread_pct=0.0003,
                latency_steps=1,
                stop_loss_pct=0.02,
                take_profit_pct=0.04,
                max_holding_bars=60,
                allow_short_selling=True,
                use_risk_sizing=True,
                risk_per_trade=0.01,
                max_leverage=2.0,
                enable_margin_checks=True,
                train_fn=self._live_train_fn,
            )

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _symbol_key(value: str) -> str:
        text = str(value or "").upper().strip()
        if ":" in text:
            text = text.split(":", 1)[0]
        raw = "".join(ch for ch in text if ch.isalnum())
        if raw.startswith("PI"):
            raw = raw[2:]
        if raw.startswith("PF"):
            raw = raw[2:]
        return raw.replace("XBT", "BTC")

    @classmethod
    def _symbols_match(cls, a: str, b: str) -> bool:
        ka = cls._symbol_key(a)
        kb = cls._symbol_key(b)
        if not ka or not kb:
            return False
        return ka == kb

    @staticmethod
    def _extract_contract_size(row: dict[str, Any]) -> float:
        info = row.get("info")
        if isinstance(info, dict):
            size = MomentumWorker._safe_float(
                info.get("contractSize", info.get("contract_size", info.get("contractValue"))),
                0.0,
            )
            if size > 0:
                return size
        size = MomentumWorker._safe_float(row.get("contractSize", row.get("contract_size")), 0.0)
        return size if size > 0 else 1.0

    @classmethod
    def _contracts_to_base_quantity(
        cls,
        symbol: str,
        contracts: float,
        price: float,
        contract_size: float,
        is_inverse: bool | None = None,
    ) -> float:
        qty_contracts = abs(float(contracts))
        if qty_contracts <= 0:
            return 0.0

        size = contract_size if contract_size > 0 else 1.0
        if is_inverse is None:
            normalized_symbol = str(symbol or "").upper()
            is_inverse = any(
                token in normalized_symbol
                for token in ("/USD:BTC", "/USD:XBT", "/USD:ETH", "/USD:SOL")
            )

        if is_inverse and price > 0:
            return (qty_contracts * size) / price
        return qty_contracts * size

    def _is_inverse_contract(self, symbol: str, row: dict[str, Any]) -> bool:
        inverse = row.get("inverse")
        if isinstance(inverse, bool):
            return inverse

        exchange = getattr(self.execution_engine, "exchange", None)
        market_symbol = str(row.get("symbol") or "").strip()
        candidates = [market_symbol, symbol]

        info = row.get("info")
        if isinstance(info, dict):
            info_symbol = str(info.get("symbol") or "").strip()
            if info_symbol:
                candidates.append(info_symbol)

        if exchange is not None:
            markets_by_id = getattr(exchange, "markets_by_id", None) or {}
            for candidate in candidates:
                if not candidate:
                    continue
                market = None
                try:
                    market = exchange.market(candidate)
                except Exception:
                    market = None

                if market is None:
                    by_id = (
                        markets_by_id.get(candidate)
                        or markets_by_id.get(str(candidate).upper())
                        or markets_by_id.get(str(candidate).lower())
                    )
                    if isinstance(by_id, list):
                        by_id = by_id[0] if by_id else None
                    if isinstance(by_id, dict):
                        market = by_id

                if isinstance(market, dict) and market.get("inverse") is not None:
                    return bool(market.get("inverse"))

        normalized_symbol = str(symbol or "").upper()
        return any(token in normalized_symbol for token in ("/USD:BTC", "/USD:XBT", "/USD:ETH", "/USD:SOL"))

    @staticmethod
    def _parse_timestamp_seconds(row: dict[str, Any]) -> float | None:
        raw_ts = row.get("timestamp")
        if isinstance(raw_ts, (int, float)):
            ts_val = float(raw_ts)
            return ts_val / 1000.0 if ts_val > 10_000_000_000 else ts_val
        if isinstance(raw_ts, str):
            text = raw_ts.strip()
            if text.isdigit():
                ts_val = float(text)
                return ts_val / 1000.0 if ts_val > 10_000_000_000 else ts_val
            parsed = MomentumWorker._parse_iso_timestamp(text)
            if parsed is not None:
                return parsed

        raw_dt = row.get("datetime")
        if isinstance(raw_dt, str):
            parsed = MomentumWorker._parse_iso_timestamp(raw_dt)
            if parsed is not None:
                return parsed

        info = row.get("info")
        if isinstance(info, dict):
            for key in ("receivedTime", "lastUpdateTimestamp", "timestamp", "time"):
                val = info.get(key)
                if isinstance(val, (int, float)):
                    ts_val = float(val)
                    return ts_val / 1000.0 if ts_val > 10_000_000_000 else ts_val
                if isinstance(val, str):
                    if val.strip().isdigit():
                        ts_val = float(val.strip())
                        return ts_val / 1000.0 if ts_val > 10_000_000_000 else ts_val
                    parsed = MomentumWorker._parse_iso_timestamp(val)
                    if parsed is not None:
                        return parsed
        return None

    @staticmethod
    def _parse_iso_timestamp(text: str) -> float | None:
        value = str(text or "").strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None

    def _find_local_position_symbol(self, symbol: str) -> str | None:
        for local_symbol in self.risk_manager.positions.keys():
            if self._symbols_match(local_symbol, symbol):
                return local_symbol
        return None

    @staticmethod
    def _parse_int_csv_env(name: str, default: list[int]) -> list[int]:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return list(default)
        out: list[int] = []
        for token in raw.split(","):
            t = token.strip()
            if not t:
                continue
            try:
                out.append(int(float(t)))
            except ValueError:
                continue
        return out or list(default)

    @staticmethod
    def _parse_float_csv_env(name: str, default: list[float]) -> list[float]:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return list(default)
        out: list[float] = []
        for token in raw.split(","):
            t = token.strip()
            if not t:
                continue
            try:
                out.append(float(t))
            except ValueError:
                continue
        return out or list(default)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        if df is None or df.empty or not {"high", "low", "close"}.issubset(df.columns):
            return 0.0
        highs = pd.to_numeric(df["high"], errors="coerce")
        lows = pd.to_numeric(df["low"], errors="coerce")
        closes = pd.to_numeric(df["close"], errors="coerce")
        prev_close = closes.shift(1)
        tr = pd.concat(
            [
                (highs - lows).abs(),
                (highs - prev_close).abs(),
                (lows - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if pd.notna(atr) else 0.0

    @staticmethod
    def _strong_reversal_against_trend(df: pd.DataFrame, side: str, sma20: float) -> bool:
        if df is None or df.empty or not {"open", "close", "high", "low"}.issubset(df.columns):
            return False
        last = df.iloc[-1]
        o = float(last.get("open", 0.0) or 0.0)
        c = float(last.get("close", 0.0) or 0.0)
        h = float(last.get("high", 0.0) or 0.0)
        l = float(last.get("low", 0.0) or 0.0)
        rng = max(1e-9, h - l)
        body_ratio = abs(c - o) / rng

        if side == "buy":
            return c < o and body_ratio >= 0.65 and c < sma20
        return c > o and body_ratio >= 0.65 and c > sma20

    @staticmethod
    def _correlation_spike(df: pd.DataFrame, window: int = 40) -> float:
        if df is None or df.empty or not {"close", "volume"}.issubset(df.columns):
            return 0.0
        close = pd.to_numeric(df["close"], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")
        ret = close.pct_change()
        vol_ret = vol.pct_change()
        data = pd.concat([ret, vol_ret], axis=1).replace([float("inf"), float("-inf")], float("nan")).dropna().tail(window)
        if data.empty:
            return 0.0
        corr = data.iloc[:, 0].corr(data.iloc[:, 1])
        return float(corr) if pd.notna(corr) else 0.0

    @staticmethod
    def _clip_score(value: float, low: float = -1.0, high: float = 1.0) -> float:
        return max(low, min(high, float(value)))

    def _signal_agreement_raw(self, lookback: int = 20) -> float:
        recent = list(self.signal_history)[-max(1, int(lookback)) :]
        buys = 0
        sells = 0
        for row in recent:
            if not isinstance(row, dict):
                continue
            side = str(row.get("side", row.get("action", "")) or "").strip().lower()
            if side == "buy":
                buys += 1
            elif side == "sell":
                sells += 1
        total = buys + sells
        if total <= 0:
            return 0.0
        return (buys - sells) / float(total)

    def _entry_gate_allows_execution(self, candles: pd.DataFrame, side: str) -> tuple[bool, str, dict[str, Any]]:
        close = pd.to_numeric(candles.get("close"), errors="coerce").dropna()
        if close.empty:
            snapshot = {"side": side, "reason": "no_candles"}
            return False, "entry_gate_failed:no_candles", snapshot

        current_price = float(close.iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else current_price
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else current_price

        trend_raw = ((sma20 / sma50) - 1.0) * 100.0 if sma50 else 0.0
        trend_score = self._clip_score(trend_raw / 0.40)
        ret_10 = float((close.iloc[-1] / close.iloc[-11] - 1.0) * 100.0) if len(close) >= 11 else 0.0
        momentum_score = self._clip_score(ret_10 / 0.60)

        returns = close.pct_change().dropna()
        vol_ann = float(returns.tail(96).std() * (24 * 365) ** 0.5) if len(returns) >= 20 else 0.0
        vol_score = self._clip_score((0.80 - vol_ann) / 0.60)

        context = self._last_context_metrics or self._compute_context_metrics(candles)
        confidence_pct = float(self._safe_float(context.get("confidence", 0.0), 0.0))
        pattern_score = float(
            self._safe_float(
                context.get("pattern_long", 0.0) if side == "buy" else context.get("pattern_short", 0.0),
                0.0,
            )
        )

        agreement_raw = float(self._signal_agreement_raw())
        agreement_score = abs(agreement_raw)

        composite = (
            0.25 * trend_score
            + 0.20 * momentum_score
            + 0.15 * vol_score
            + 0.10 * pattern_score
            + 0.20 * self._clip_score((confidence_pct - 50.0) / 25.0)
            + 0.10 * self._clip_score(agreement_raw)
        )

        confidence_gate = confidence_pct >= self.entry_confidence_gate_pct
        conviction_gate = abs(composite) >= self.entry_conviction_gate
        trend_gate = trend_score >= 0.30
        vol_gate = vol_score >= 0.0
        pattern_gate = pattern_score >= 0.25
        agreement_gate = agreement_score >= self.entry_agreement_gate

        if side == "buy":
            direction_gate = composite >= self.entry_conviction_gate
        else:
            direction_gate = composite <= -self.entry_conviction_gate

        allowed = (
            confidence_gate
            and conviction_gate
            and trend_gate
            and vol_gate
            and pattern_gate
            and agreement_gate
            and direction_gate
        )

        snapshot: dict[str, Any] = {
            "side": side,
            "confidence_pct": confidence_pct,
            "composite": float(composite),
            "trend_score": float(trend_score),
            "vol_score": float(vol_score),
            "pattern_score": float(pattern_score),
            "agreement_raw": float(agreement_raw),
            "agreement_score": float(agreement_score),
            "confidence_gate": confidence_gate,
            "conviction_gate": conviction_gate,
            "trend_gate": trend_gate,
            "vol_gate": vol_gate,
            "pattern_gate": pattern_gate,
            "agreement_gate": agreement_gate,
            "direction_gate": direction_gate,
        }

        if allowed:
            return True, "entry_gate_pass", snapshot

        if not confidence_gate:
            return False, "entry_gate_failed:confidence", snapshot
        if not conviction_gate:
            return False, "entry_gate_failed:conviction", snapshot
        if not trend_gate:
            return False, "entry_gate_failed:trend", snapshot
        if not vol_gate:
            return False, "entry_gate_failed:volatility", snapshot
        if not pattern_gate:
            return False, "entry_gate_failed:pattern", snapshot
        if not agreement_gate:
            return False, "entry_gate_failed:agreement", snapshot
        if not direction_gate:
            return False, "entry_gate_failed:direction", snapshot
        return False, "entry_gate_failed:unknown", snapshot

    def _compute_context_metrics(self, candles: pd.DataFrame) -> dict[str, Any]:
        if candles is None or candles.empty or "close" not in candles.columns:
            return {}

        close = pd.to_numeric(candles["close"], errors="coerce").dropna()
        if close.empty:
            return {}
        open_ = pd.to_numeric(candles.get("open"), errors="coerce")
        high = pd.to_numeric(candles.get("high"), errors="coerce")
        low = pd.to_numeric(candles.get("low"), errors="coerce")
        volume = pd.to_numeric(candles.get("volume"), errors="coerce")

        current_price = float(close.iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else current_price
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
        trend_pct = ((sma20 / sma50) - 1.0) * 100.0 if sma50 else 0.0
        trend_abs = abs(trend_pct) / 100.0
        trend_score = min(1.0, trend_abs / 0.01)

        momentum = 0.0
        if len(close) >= max(2, int(self.momentum_period)):
            ref = float(close.iloc[-int(self.momentum_period)])
            if ref:
                momentum = ((current_price - ref) / ref) * 100.0
        momentum_score = min(1.0, abs(momentum) / 2.5)

        candle_df = candles.copy()
        if {"open", "close", "high", "low"}.issubset(candle_df.columns):
            rng = (high - low).astype("float64")
            rng = rng.mask(rng == 0.0, 1e-9).fillna(1e-9)
            bullish_body = ((close - open_).clip(lower=0.0) / rng).tail(5).mean()
            bearish_body = ((open_ - close).clip(lower=0.0) / rng).tail(5).mean()
            bullish_body = float(0.0 if pd.isna(bullish_body) else bullish_body)
            bearish_body = float(0.0 if pd.isna(bearish_body) else bearish_body)
        else:
            bullish_body = 0.0
            bearish_body = 0.0

        close_diff = close.diff().dropna().tail(12)
        up_ratio = float((close_diff > 0).mean()) if not close_diff.empty else 0.0
        down_ratio = float((close_diff < 0).mean()) if not close_diff.empty else 0.0

        confidence_score = min(99.0, max(5.0, abs(trend_pct + momentum) * 25.0))
        composite_long = (
            0.30 * trend_score
            + 0.30 * momentum_score
            + 0.20 * bullish_body
            + 0.20 * up_ratio
        )
        composite_short = (
            0.30 * trend_score
            + 0.30 * momentum_score
            + 0.20 * bearish_body
            + 0.20 * down_ratio
        )

        atr = self._atr(candles, period=14)
        vol_ma20 = float(volume.tail(20).mean()) if not volume.dropna().empty else 0.0
        vol_last = float(volume.iloc[-1]) if len(volume) else 0.0
        correlation = self._correlation_spike(candles)

        return {
            "price": current_price,
            "sma20": sma20,
            "sma50": sma50,
            "trend_score": float(trend_score),
            "trend_pct": float(trend_pct),
            "momentum": float(momentum),
            "confidence": float(confidence_score),
            "pattern_long": float(bullish_body),
            "pattern_short": float(bearish_body),
            "imbalance_long": float(up_ratio),
            "imbalance_short": float(down_ratio),
            "composite_long": float(composite_long),
            "composite_short": float(composite_short),
            "atr": float(atr),
            "vol_ma20": float(vol_ma20),
            "vol_last": float(vol_last),
            "correlation": float(correlation),
            "reversal_long": self._strong_reversal_against_trend(candles, "buy", sma20),
            "reversal_short": self._strong_reversal_against_trend(candles, "sell", sma20),
        }

    def _momentum_strategy_for_backtest(
        self,
        data: pd.DataFrame,
        momentum_period: int = 14,
        buy_threshold: float = 1.0,
        sell_threshold: float = -1.0,
    ) -> list[dict[str, Any]]:
        if data is None or data.empty or "close" not in data.columns:
            return []
        close = pd.to_numeric(data["close"], errors="coerce")
        signals: list[dict[str, Any]] = []
        period = max(2, int(momentum_period))
        for i in range(period, len(close)):
            base = float(close.iloc[i - period] or 0.0)
            if base == 0.0:
                continue
            momentum = ((float(close.iloc[i]) - base) / base) * 100.0
            if momentum > float(buy_threshold):
                signals.append({"index": i, "action": "buy", "quantity": 0.001})
            elif momentum < float(sell_threshold):
                signals.append({"index": i, "action": "sell", "quantity": 0.001})
        return signals

    def _live_train_fn(self, train_df: pd.DataFrame) -> dict[str, Any]:
        if train_df is None or train_df.empty or len(train_df) < self.auto_train_min_rows:
            return {
                "momentum_period": int(self.momentum_period),
                "buy_threshold": float(self.buy_threshold),
                "sell_threshold": float(self.sell_threshold),
            }

        best_params = {
            "momentum_period": int(self.momentum_period),
            "buy_threshold": float(self.buy_threshold),
            "sell_threshold": float(self.sell_threshold),
        }
        best_score = float("-inf")

        for period in self.auto_train_period_grid:
            for buy_th in self.auto_train_buy_grid:
                for sell_th in self.auto_train_sell_grid:
                    engine = BacktestEngine(
                        strategy_fn=self._momentum_strategy_for_backtest,
                        initial_balance=float(self.account_balance),
                        fee_rate=0.0006,
                        slippage_pct=0.0005,
                        spread_pct=0.0003,
                        latency_steps=1,
                        stop_loss_pct=0.02,
                        take_profit_pct=0.04,
                        max_holding_bars=40,
                        allow_short_selling=True,
                        use_risk_sizing=True,
                        risk_per_trade=0.01,
                        max_leverage=2.0,
                        enable_margin_checks=True,
                        train_fn=None,
                    )
                    trades = engine.run(
                        train_df,
                        params={
                            "momentum_period": int(period),
                            "buy_threshold": float(buy_th),
                            "sell_threshold": float(sell_th),
                        },
                    )
                    if trades.empty:
                        continue

                    pnl_series = pd.to_numeric(trades.get("pnl"), errors="coerce").dropna()
                    total_pnl = float(pnl_series.sum()) if not pnl_series.empty else 0.0
                    wins = pnl_series[pnl_series > 0]
                    losses = pnl_series[pnl_series < 0]
                    gross_profit = float(wins.sum()) if not wins.empty else 0.0
                    gross_loss = float(-losses.sum()) if not losses.empty else 0.0
                    profit_factor = (
                        2.0
                        if gross_loss == 0 and gross_profit > 0
                        else (gross_profit / gross_loss if gross_loss > 0 else 0.0)
                    )
                    score = total_pnl + (profit_factor * 10.0)

                    if score > best_score:
                        best_score = score
                        best_params = {
                            "momentum_period": int(period),
                            "buy_threshold": float(buy_th),
                            "sell_threshold": float(sell_th),
                        }

        return best_params

    async def _maybe_auto_train(self, candles: pd.DataFrame) -> None:
        if not self.live_auto_train_enabled:
            return
        if candles is None or candles.empty or len(candles) < self.auto_train_min_rows:
            return
        if self.live_train_engine is None:
            return

        self._auto_train_iter_count += 1
        if (self._auto_train_iter_count - self._auto_train_last_iter) < self.auto_train_every_n_iters:
            return

        train_df = candles.tail(self.auto_train_lookback_rows).copy()
        params = await asyncio.to_thread(self.live_train_engine.train_live, train_df, self.auto_train_lookback_rows)
        if not params:
            return

        period = int(params.get("momentum_period", self.momentum_period))
        buy_th = float(params.get("buy_threshold", self.buy_threshold))
        sell_th = float(params.get("sell_threshold", self.sell_threshold))

        if (
            period != int(self.momentum_period)
            or abs(buy_th - float(self.buy_threshold)) > 1e-12
            or abs(sell_th - float(self.sell_threshold)) > 1e-12
        ):
            self.momentum_period = period
            self.buy_threshold = buy_th
            self.sell_threshold = sell_th
            if hasattr(self.strategy, "momentum_period"):
                self.strategy.momentum_period = period
            if hasattr(self.strategy, "buy_threshold"):
                self.strategy.buy_threshold = buy_th
            if hasattr(self.strategy, "sell_threshold"):
                self.strategy.sell_threshold = sell_th
            logger.info(
                "Live auto-training updated params | period=%s buy=%.3f sell=%.3f",
                period,
                buy_th,
                sell_th,
            )
        self._auto_train_last_iter = self._auto_train_iter_count

    def _ensure_position_guard(self, symbol: str, side: str, entry_price: float) -> dict[str, Any]:
        guard = self.position_guards.get(symbol)
        side = str(side).lower()
        entry_price = float(entry_price)
        should_reset = (
            guard is None
            or str(guard.get("side", "")).lower() != side
            or abs(float(guard.get("entry_price", entry_price)) - entry_price) > max(1e-9, entry_price * 0.0001)
        )
        if should_reset:
            guard = {
                "side": side,
                "entry_price": entry_price,
                "entry_momentum": float(self._safe_float(self._last_context_metrics.get("momentum"), 0.0)),
                "entry_atr": float(self._safe_float(self._last_context_metrics.get("atr"), 0.0)),
                "entry_candle_index": len(self.candle_history),
                "highest_price": entry_price,
                "lowest_price": entry_price,
                "stop_price": (
                    entry_price * (1.0 - self.exit_max_loss_pct)
                    if side == "buy"
                    else entry_price * (1.0 + self.exit_max_loss_pct)
                ),
                "risk_r": float(self.exit_max_loss_pct),
            }
            self.position_guards[symbol] = guard
        return guard

    def _build_exit_signal_if_needed(self, candles: pd.DataFrame) -> dict[str, Any] | None:
        if not self.risk_manager.positions:
            return None
        context = self._compute_context_metrics(candles)
        if not context:
            return None
        self._last_context_metrics = context
        current_price = float(context.get("price", 0.0))
        if current_price <= 0:
            return None

        for symbol, pos in list(self.risk_manager.positions.items()):
            side = str(pos.get("side", "")).lower()
            if side not in {"buy", "sell"}:
                continue
            entry_price = float(pos.get("entry_price", 0.0) or 0.0)
            quantity = float(pos.get("quantity", 0.0) or 0.0)
            if entry_price <= 0 or quantity <= 0:
                continue

            guard = self._ensure_position_guard(symbol, side, entry_price)
            guard["highest_price"] = max(float(guard.get("highest_price", entry_price)), current_price)
            guard["lowest_price"] = min(float(guard.get("lowest_price", entry_price)), current_price)
            bars_held = max(0, len(self.candle_history) - int(guard.get("entry_candle_index", len(self.candle_history))))

            if side == "buy":
                unrealized_pct = (current_price - entry_price) / entry_price
                loss_pct = max(0.0, -unrealized_pct)
                confidence = float(context.get("confidence", 0.0))
                composite = float(context.get("composite_long", 0.0))
                trend = float(context.get("trend_score", 0.0))
                pattern = float(context.get("pattern_long", 0.0))
                imbalance = float(context.get("imbalance_long", 0.0))
                opposite_imbalance = float(context.get("imbalance_short", 0.0))

                if loss_pct >= self.exit_max_loss_pct:
                    reason = "loss_cut_2pct"
                elif confidence < 48.0:
                    reason = "confidence_below_48"
                elif composite <= 0.15:
                    reason = "composite_below_0.15"
                elif trend <= 0.05:
                    reason = "trend_below_0.05"
                elif pattern <= 0.10:
                    reason = "pattern_below_0.10"
                elif imbalance <= 0.05:
                    reason = "imbalance_below_0.05"
                elif opposite_imbalance >= 0.25:
                    reason = "opposite_imbalance_spike"
                else:
                    reason = ""

                r_value = float(guard.get("risk_r", self.exit_max_loss_pct))
                if unrealized_pct >= (1.0 * r_value):
                    guard["stop_price"] = max(float(guard.get("stop_price", entry_price)), entry_price)
                if unrealized_pct >= (2.0 * r_value):
                    trailing = current_price * (1.0 - (0.75 * r_value))
                    guard["stop_price"] = max(float(guard.get("stop_price", entry_price)), trailing)

                if current_price <= float(guard.get("stop_price", entry_price)):
                    reason = reason or "profit_lock_stop_hit"
                if bool(context.get("reversal_long", False)):
                    reason = reason or "strong_reversal_against_long"
                if bars_held >= self.exit_time_stop_bars:
                    entry_momentum = abs(float(guard.get("entry_momentum", 0.0)))
                    current_momentum = abs(float(context.get("momentum", 0.0)))
                    if current_momentum <= (entry_momentum * 1.05):
                        reason = reason or "time_stop_no_momentum_expansion"
                entry_atr = float(guard.get("entry_atr", 0.0))
                current_atr = float(context.get("atr", 0.0))
                if entry_atr > 0 and current_atr > 0 and current_atr <= (entry_atr * self.exit_volatility_contraction):
                    reason = reason or "volatility_contraction_exit"
                vol_ma20 = float(context.get("vol_ma20", 0.0))
                vol_last = float(context.get("vol_last", 0.0))
                if vol_ma20 > 0 and vol_last < (vol_ma20 * self.exit_liquidity_vacuum_factor):
                    reason = reason or "liquidity_vacuum_exit"
                corr = abs(float(context.get("correlation", 0.0)))
                if corr >= self.exit_correlation_spike_abs:
                    reason = reason or "correlation_spike_exit"

                if reason:
                    return {
                        "symbol": symbol,
                        "side": "sell",
                        "quantity": quantity,
                        "order_type": "market",
                        "order_kind": "taker",
                        "strategy_id": "momentum_exit_v1",
                        "regime": "risk_exit",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "exit_reason": reason,
                    }
            else:
                unrealized_pct = (entry_price - current_price) / entry_price
                loss_pct = max(0.0, -unrealized_pct)
                confidence = float(context.get("confidence", 0.0))
                composite = float(context.get("composite_short", 0.0))
                trend = float(context.get("trend_score", 0.0))
                pattern = float(context.get("pattern_short", 0.0))
                imbalance = float(context.get("imbalance_short", 0.0))
                opposite_imbalance = float(context.get("imbalance_long", 0.0))

                if loss_pct >= self.exit_max_loss_pct:
                    reason = "loss_cut_2pct_short"
                elif confidence < 48.0:
                    reason = "confidence_below_48_short"
                elif composite <= 0.15:
                    reason = "composite_below_0.15_short"
                elif trend <= 0.05:
                    reason = "trend_below_0.05_short"
                elif pattern <= 0.10:
                    reason = "pattern_below_0.10_short"
                elif imbalance <= 0.05:
                    reason = "imbalance_below_0.05_short"
                elif opposite_imbalance >= 0.25:
                    reason = "opposite_imbalance_spike_short"
                else:
                    reason = ""

                r_value = float(guard.get("risk_r", self.exit_max_loss_pct))
                if unrealized_pct >= (1.0 * r_value):
                    guard["stop_price"] = min(float(guard.get("stop_price", entry_price)), entry_price)
                if unrealized_pct >= (2.0 * r_value):
                    trailing = current_price * (1.0 + (0.75 * r_value))
                    guard["stop_price"] = min(float(guard.get("stop_price", entry_price)), trailing)

                if current_price >= float(guard.get("stop_price", entry_price)):
                    reason = reason or "profit_lock_stop_hit_short"
                if bool(context.get("reversal_short", False)):
                    reason = reason or "strong_reversal_against_short"
                if bars_held >= self.exit_time_stop_bars:
                    entry_momentum = abs(float(guard.get("entry_momentum", 0.0)))
                    current_momentum = abs(float(context.get("momentum", 0.0)))
                    if current_momentum <= (entry_momentum * 1.05):
                        reason = reason or "time_stop_no_momentum_expansion_short"
                entry_atr = float(guard.get("entry_atr", 0.0))
                current_atr = float(context.get("atr", 0.0))
                if entry_atr > 0 and current_atr > 0 and current_atr <= (entry_atr * self.exit_volatility_contraction):
                    reason = reason or "volatility_contraction_exit_short"
                vol_ma20 = float(context.get("vol_ma20", 0.0))
                vol_last = float(context.get("vol_last", 0.0))
                if vol_ma20 > 0 and vol_last < (vol_ma20 * self.exit_liquidity_vacuum_factor):
                    reason = reason or "liquidity_vacuum_exit_short"
                corr = abs(float(context.get("correlation", 0.0)))
                if corr >= self.exit_correlation_spike_abs:
                    reason = reason or "correlation_spike_exit_short"

                if reason:
                    return {
                        "symbol": symbol,
                        "side": "buy",
                        "quantity": quantity,
                        "order_type": "market",
                        "order_kind": "taker",
                        "strategy_id": "momentum_exit_v1",
                        "regime": "risk_exit",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "exit_reason": reason,
                    }

        return None

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

    def _reference_price(self, signal: Dict[str, Any]) -> float | None:
        try:
            value = float(signal.get("price", 0.0) or 0.0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

        if self.candle_history:
            last = self.candle_history[-1]
            if isinstance(last, dict):
                try:
                    close = float(last.get("close", 0.0) or 0.0)
                    if close > 0:
                        return close
                except (TypeError, ValueError):
                    pass

        return None

    def _entry_confidence_pct(self, signal: Dict[str, Any]) -> float:
        candidates = [
            signal.get("confidence_pct"),
            signal.get("confidence"),
            self._last_context_metrics.get("confidence"),
        ]
        for value in candidates:
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            # Normalize 0-1 confidence values to percentages.
            if 0.0 <= score <= 1.0:
                score *= 100.0
            if score >= 0.0:
                return min(100.0, score)
        return 0.0

    def _apply_live_order_preferences(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        if getattr(self.execution_engine, "paper_mode", True):
            return signal

        side = str(signal.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            return signal

        confidence_pct = self._entry_confidence_pct(signal)
        if self.live_taker_high_confidence and confidence_pct >= self.live_taker_confidence_threshold:
            reference_price = self._reference_price(signal)
            signal["order_kind"] = "taker"
            signal["order_type"] = "market"
            if reference_price is not None and reference_price > 0:
                signal["expected_price"] = signal.get("expected_price") or reference_price
            signal.pop("price", None)
            logger.info(
                "Applied high-confidence taker preference | side=%s confidence=%.2f threshold=%.2f",
                side,
                confidence_pct,
                self.live_taker_confidence_threshold,
            )
            return signal

        if not self.live_maker_only:
            return signal

        reference_price = self._reference_price(signal)
        if reference_price is None or reference_price <= 0:
            logger.warning("Skipping maker preference; no valid reference price in signal=%s", signal)
            return signal

        offset = self.live_maker_offset_bps / 10_000.0
        maker_price = reference_price * (1.0 - offset) if side == "buy" else reference_price * (1.0 + offset)

        signal["order_kind"] = "maker"
        signal["order_type"] = "limit"
        signal["expected_price"] = signal.get("expected_price") or reference_price
        signal["price"] = float(maker_price)

        logger.info(
            "Applied live maker preference | side=%s ref=%.2f maker=%.2f offset_bps=%.1f",
            side,
            reference_price,
            signal["price"],
            self.live_maker_offset_bps,
        )
        return signal

    def _bot_order_ids(self) -> set[str]:
        ids: set[str] = set()
        for row in self.signal_history:
            if not isinstance(row, dict):
                continue
            order_id = str(row.get("order_id") or "").strip()
            if order_id:
                ids.add(order_id)
        return ids

    def _has_pending_bot_order(self, symbol: str) -> bool:
        bot_ids = self._bot_order_ids()
        if not bot_ids:
            return False

        for row in self.open_orders_snapshot:
            if not isinstance(row, dict):
                continue
            order_id = str(row.get("id") or "").strip()
            if not order_id or order_id not in bot_ids:
                continue
            order_symbol = str(row.get("symbol") or "")
            if symbol and order_symbol and not self._symbols_match(symbol, order_symbol):
                continue
            status = str(row.get("status") or "").lower()
            if status in {"open", "new", "pending", "submitted"}:
                return True
        return False

    def _is_post_only_order(self, row: dict[str, Any]) -> bool:
        if bool(row.get("postOnly")):
            return True
        info = row.get("info")
        if isinstance(info, dict):
            if bool(info.get("postOnly")):
                return True
            order_type = str(info.get("orderType") or "").lower()
            if "post" in order_type:
                return True
        return False

    def _fetch_exchange_state_sync(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        exchange = getattr(self.execution_engine, "exchange", None)
        if exchange is None:
            self.last_open_orders_fetch_ok = False
            self.last_positions_fetch_ok = False
            return [], []

        open_orders: list[dict[str, Any]] = []
        positions: list[dict[str, Any]] = []
        open_orders_fetch_ok = False
        positions_fetch_ok = False

        try:
            rows = exchange.fetch_open_orders()
            open_orders_fetch_ok = True
            if isinstance(rows, list):
                open_orders = rows
        except Exception as exc:
            logger.debug("fetch_open_orders failed: %s", exc)
            try:
                rows = exchange.fetch_open_orders(self.symbol)
                open_orders_fetch_ok = True
                if isinstance(rows, list):
                    open_orders = rows
            except Exception as exc2:
                logger.debug("fetch_open_orders(symbol) failed: %s", exc2)

        try:
            rows = exchange.fetch_positions()
            positions_fetch_ok = True
            if isinstance(rows, list):
                positions = rows
        except Exception as exc:
            logger.debug("fetch_positions failed: %s", exc)
            try:
                rows = exchange.fetch_positions([self.symbol])
                positions_fetch_ok = True
                if isinstance(rows, list):
                    positions = rows
            except Exception as exc2:
                logger.debug("fetch_positions(symbol) failed: %s", exc2)

        self.last_open_orders_fetch_ok = open_orders_fetch_ok
        self.last_positions_fetch_ok = positions_fetch_ok
        return open_orders, positions

    def _cancel_stale_bot_orders_sync(self, open_orders: list[dict[str, Any]]) -> set[str]:
        if not self.cancel_stale_orders:
            return set()
        exchange = getattr(self.execution_engine, "exchange", None)
        if exchange is None:
            return set()

        bot_ids = self._bot_order_ids()
        if not bot_ids:
            return set()

        now_ts = datetime.now(timezone.utc).timestamp()
        canceled: set[str] = set()

        for row in open_orders:
            if len(canceled) >= self.max_stale_cancels_per_iter:
                break
            if not isinstance(row, dict):
                continue
            order_id = str(row.get("id") or "").strip()
            if not order_id or order_id not in bot_ids:
                continue

            status = str(row.get("status") or "").lower()
            if status not in {"open", "new", "pending", "submitted"}:
                continue
            if not self._is_post_only_order(row):
                continue

            created_ts = self._parse_timestamp_seconds(row)
            if created_ts is None or (now_ts - created_ts) < self.cancel_stale_after_sec:
                continue

            order_symbol = str(row.get("symbol") or self.symbol)
            try:
                exchange.cancel_order(order_id, order_symbol or None)
                canceled.add(order_id)
                logger.info(
                    "Canceled stale maker order | id=%s symbol=%s age_sec=%.1f",
                    order_id,
                    order_symbol,
                    now_ts - created_ts,
                )
            except Exception as exc:
                logger.warning("Failed canceling stale maker order %s: %s", order_id, exc)

        return canceled

    def _sync_signal_history_from_exchange(
        self,
        open_orders: list[dict[str, Any]],
        positions: list[dict[str, Any]],
        canceled_ids: set[str],
        open_orders_fresh: bool,
    ) -> None:
        open_order_by_id: dict[str, dict[str, Any]] = {}
        for row in open_orders:
            if not isinstance(row, dict):
                continue
            order_id = str(row.get("id") or "").strip()
            if order_id:
                open_order_by_id[order_id] = row

        position_rows: list[dict[str, Any]] = []
        for row in positions:
            if not isinstance(row, dict):
                continue
            if self._safe_float(row.get("contracts"), 0.0) == 0.0:
                continue
            position_rows.append(row)

        now_ts = datetime.now(timezone.utc).timestamp()
        for row in self.signal_history:
            if not isinstance(row, dict):
                continue
            order_id = str(row.get("order_id") or "").strip()
            if not order_id:
                continue

            status = str(row.get("status") or "").lower()
            symbol = str(row.get("symbol") or "")
            open_order = open_order_by_id.get(order_id)

            if open_order is not None:
                row["status"] = "submitted"
                amount = self._safe_float(open_order.get("amount"), 0.0)
                remaining = self._safe_float(open_order.get("remaining"), amount if amount > 0 else 0.0)
                if amount > 0:
                    row["filled"] = max(0.0, amount - remaining)
                continue

            if order_id in canceled_ids:
                row["status"] = "cancelled"
                continue

            if status in {"submitted", "pending", "open", "partial"}:
                matched_position = None
                for pos in position_rows:
                    pos_symbol = str(pos.get("id") or pos.get("symbol") or "")
                    if self._symbols_match(symbol, pos_symbol):
                        matched_position = pos
                        break

                if matched_position is not None:
                    row["status"] = "filled"
                    entry_price = self._safe_float(matched_position.get("entryPrice"), 0.0)
                    if entry_price > 0 and self._safe_float(row.get("avg_fill_price"), 0.0) <= 0:
                        row["avg_fill_price"] = entry_price
                        row["entry_price"] = entry_price
                    if self._safe_float(row.get("filled"), 0.0) <= 0:
                        qty = self._safe_float(row.get("quantity"), 0.0)
                        if qty > 0:
                            row["filled"] = qty
                    continue

                if open_orders_fresh:
                    created_ts = self._parse_timestamp_seconds(row)
                    if created_ts is not None and (now_ts - created_ts) >= self.cancel_stale_after_sec:
                        row["status"] = "cancelled"

    def _sync_positions_from_exchange(self, positions: list[dict[str, Any]]) -> None:
        exchange_positions: dict[str, dict[str, Any]] = {}
        for row in positions:
            if not isinstance(row, dict):
                continue
            contracts = self._safe_float(row.get("contracts"), 0.0)
            if contracts == 0:
                continue
            info = row.get("info")
            info_symbol = ""
            if isinstance(info, dict):
                info_symbol = str(info.get("symbol") or "").strip()
            symbol = str(row.get("id") or info_symbol or row.get("symbol") or self.symbol)
            side_raw = str(row.get("side") or "").lower()
            if side_raw in {"buy", "long"}:
                side = "buy"
            elif side_raw in {"sell", "short"}:
                side = "sell"
            else:
                side = "buy" if contracts > 0 else "sell"
            entry_price = self._safe_float(row.get("entryPrice"), 0.0)
            mark_price = self._safe_float(row.get("markPrice"), entry_price)
            if mark_price <= 0:
                mark_price = entry_price
            if entry_price <= 0:
                entry_price = mark_price
            contract_size = self._extract_contract_size(row)
            is_inverse = self._is_inverse_contract(symbol=symbol, row=row)
            quantity = self._contracts_to_base_quantity(
                symbol=symbol,
                contracts=contracts,
                price=mark_price if mark_price > 0 else entry_price,
                contract_size=contract_size,
                is_inverse=is_inverse,
            )
            if quantity <= 0:
                quantity = abs(contracts)
            if entry_price <= 0 or quantity <= 0:
                continue
            exchange_positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "mark_price": mark_price if mark_price > 0 else entry_price,
                "contracts": abs(contracts),
            }

        for local_symbol in list(self.risk_manager.positions.keys()):
            still_open = any(self._symbols_match(local_symbol, sym) for sym in exchange_positions.keys())
            if still_open:
                continue
            cached = self.exchange_position_cache.get(local_symbol, {})
            exit_price = self._safe_float(cached.get("mark_price"), 0.0)
            if exit_price <= 0:
                exit_price = self._reference_price({}) or self._safe_float(
                    self.risk_manager.positions.get(local_symbol, {}).get("entry_price"),
                    0.0,
                )
            if exit_price > 0:
                pnl = self.risk_manager.close_position(local_symbol, float(exit_price))
                logger.info("Synced exchange close | symbol=%s exit=%.2f pnl=%.4f", local_symbol, exit_price, pnl)
                self.position_guards.pop(local_symbol, None)

        new_cache: dict[str, dict[str, Any]] = {}
        for exchange_symbol, row in exchange_positions.items():
            preferred_symbol = self.symbol if self._symbols_match(self.symbol, exchange_symbol) else exchange_symbol
            local_symbol = self._find_local_position_symbol(preferred_symbol) or preferred_symbol

            if local_symbol not in self.risk_manager.positions:
                self.risk_manager.open_position(
                    local_symbol,
                    str(row["side"]),
                    float(row["quantity"]),
                    float(row["entry_price"]),
                )
                self._ensure_position_guard(
                    symbol=local_symbol,
                    side=str(row["side"]),
                    entry_price=float(row["entry_price"]),
                )
                logger.info(
                    "Synced exchange open | symbol=%s side=%s qty=%.6f entry=%.2f",
                    local_symbol,
                    row["side"],
                    float(row["quantity"]),
                    float(row["entry_price"]),
                )
            else:
                local_pos = self.risk_manager.positions[local_symbol]
                prev_quantity = self._safe_float(local_pos.get("quantity"), 0.0)
                local_pos["side"] = row["side"]
                local_pos["quantity"] = float(row["quantity"])
                local_pos["entry_price"] = float(row["entry_price"])
                if prev_quantity > 0 and float(row["quantity"]) > (prev_quantity * 10.0):
                    logger.warning(
                        "Synced position quantity jump | symbol=%s prev=%.8f new=%.8f contracts=%s",
                        local_symbol,
                        prev_quantity,
                        float(row["quantity"]),
                        row.get("contracts"),
                    )
                self._ensure_position_guard(
                    symbol=local_symbol,
                    side=str(row["side"]),
                    entry_price=float(row["entry_price"]),
                )

            new_cache[local_symbol] = dict(row)

        self.exchange_position_cache = new_cache

    async def _sync_live_exchange_state(self) -> None:
        if getattr(self.execution_engine, "paper_mode", True):
            return
        if not self.sync_exchange_state:
            return

        try:
            open_orders, positions = await asyncio.to_thread(self._fetch_exchange_state_sync)
            canceled_ids: set[str] = set()
            if self.last_open_orders_fetch_ok:
                if open_orders and self.cancel_stale_orders:
                    canceled_ids = await asyncio.to_thread(self._cancel_stale_bot_orders_sync, open_orders)
                    if canceled_ids:
                        open_orders = [
                            row
                            for row in open_orders
                            if str((row or {}).get("id") or "").strip() not in canceled_ids
                        ]
                self.open_orders_snapshot = open_orders

            self._sync_signal_history_from_exchange(
                open_orders=open_orders if self.last_open_orders_fetch_ok else self.open_orders_snapshot,
                positions=positions if self.last_positions_fetch_ok else [],
                canceled_ids=canceled_ids,
                open_orders_fresh=self.last_open_orders_fetch_ok,
            )
            if self.last_positions_fetch_ok:
                self._sync_positions_from_exchange(positions)
        except Exception:
            logger.exception("Failed syncing live exchange state")

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
            self.last_decision_reason = "iteration_started"
            await self._sync_live_exchange_state()

            status = self.risk_manager.get_status()
            drawdown = status.get("drawdown_pct", 0)
            if drawdown > 20.0:
                logger.critical("🛑 MAX DRAWDOWN BREACHED (%.2f%%) - STOPPING STRATEGY", drawdown)
                self.last_decision_reason = "stopped_max_drawdown"
                await self.stop()
                return

            ohlcv = await self._load_ohlcv(
                symbol=self.symbol,
                timeframe="1h",
                limit=50,
            )
            if ohlcv is None:
                logger.warning("Insufficient data for %s: 0 < %s", self.symbol, self.momentum_period)
                self.last_decision_reason = "insufficient_data_none"
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
                self.last_decision_reason = "insufficient_data_short"
                return

            await self._maybe_auto_train(candles)
            self._last_context_metrics = self._compute_context_metrics(candles)

            exit_signal = self._build_exit_signal_if_needed(candles)
            if exit_signal is not None:
                if self._has_pending_bot_order(str(exit_signal.get("symbol", self.symbol))):
                    logger.info(
                        "Exit signal queued but pending bot order exists for %s; waiting.",
                        exit_signal.get("symbol", self.symbol),
                    )
                    self.last_decision_reason = "exit_pending_order"
                    return
                logger.info("Executing exit logic signal: %s", exit_signal)
                self.last_decision_reason = f"executing_exit:{str(exit_signal.get('exit_reason', 'unknown'))}"
                exit_result = self.execution_engine.execute(exit_signal)
                exit_status = str((exit_result or {}).get("status", "")).lower()
                if exit_status in {"rejected", "cancelled", "canceled"}:
                    detail = str(
                        (exit_result or {}).get("reason")
                        or (exit_result or {}).get("error")
                        or exit_status
                    )
                    self.last_decision_reason = f"exit_rejected:{detail}"
                order_record, trade_record = self._build_order_record(exit_signal, exit_result)
                self.signal_history.append(order_record)
                if trade_record:
                    self.trade_history.append(trade_record)
                    self._persist_trade(trade_record)
                if exit_result and exit_status not in {"rejected", "cancelled", "canceled"}:
                    self.last_decision_reason = "exit_submitted"
                elif not exit_result:
                    self.last_decision_reason = "exit_execution_returned_none"
                await self._sync_live_exchange_state()
                return

            signal = await self._generate_signal(candles)
            if not signal:
                logger.info("No signal generated")
                self.last_decision_reason = "no_signal_generated"
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
            if self.enforce_execution_gates and side in {"buy", "sell"}:
                gates_ok, gate_reason, gate_snapshot = self._entry_gate_allows_execution(candles, side)
                self.last_entry_gate_snapshot = gate_snapshot
                signal["gate_snapshot"] = gate_snapshot
                if not gates_ok:
                    logger.info(
                        "Skipping signal: entry gate failed (%s) | side=%s snapshot=%s",
                        gate_reason,
                        side,
                        gate_snapshot,
                    )
                    self.last_decision_reason = gate_reason
                    return

            signal = self._apply_live_order_preferences(signal)

            symbol = str(signal.get("symbol", self.symbol))
            local_position_symbol = self._find_local_position_symbol(symbol)

            if self._has_pending_bot_order(symbol):
                logger.info(
                    "Skipping signal: pending bot order already open for symbol=%s",
                    symbol,
                )
                self.last_decision_reason = "entry_pending_order"
                return

            if local_position_symbol:
                local_side = str(self.risk_manager.positions.get(local_position_symbol, {}).get("side", "")).lower()
                if local_side and local_side != side:
                    allowed, reason = True, "closing position"
                    signal["symbol"] = local_position_symbol
                else:
                    logger.info(
                        "Skipping signal: position already open for %s side=%s",
                        local_position_symbol,
                        local_side or side,
                    )
                    self.last_decision_reason = "position_open_same_side"
                    return
            else:
                allowed, reason = self.risk_manager.check_risk_limits(signal)

            if not allowed:
                logger.warning("Signal blocked by risk manager: %s | signal=%s", reason, signal)
                self.last_decision_reason = f"risk_blocked:{reason}"
                return

            try:
                logger.info("Executing momentum signal: %s", signal)
                self.last_decision_reason = "executing_entry"
                result = self.execution_engine.execute(signal)
                result_status = str((result or {}).get("status", "")).lower()
                if result and result_status in {"rejected", "cancelled", "canceled"}:
                    order_record, trade_record = self._build_order_record(signal, result)
                    self.signal_history.append(order_record)
                    detail = str(
                        (result or {}).get("reason")
                        or (result or {}).get("error")
                        or result_status
                    )
                    self.last_decision_reason = f"entry_rejected:{detail}"
                    logger.warning("Execution rejected: %s", detail)
                    await self._sync_live_exchange_state()
                elif result:
                    self.execution_count += 1
                    self.trade_count += 1

                    order_record, trade_record = self._build_order_record(signal, result)
                    self.signal_history.append(order_record)
                    if trade_record:
                        self.trade_history.append(trade_record)
                        self._persist_trade(trade_record)

                    if self.max_trades and self.trade_count >= self.max_trades:
                        logger.info("Reached max trades (%s). Stopping worker.", self.max_trades)
                        self.last_decision_reason = "stopped_max_trades"
                        await self.stop()
                        return

                    logger.info("Order placed: %s", result.get("id", result))
                    self.last_decision_reason = "entry_submitted"
                    await self._sync_live_exchange_state()
                else:
                    order_record, trade_record = self._build_order_record(signal, None)
                    self.signal_history.append(order_record)
                    logger.warning("Execution returned no result")
                    self.last_decision_reason = "entry_execution_returned_none"
                    await self._sync_live_exchange_state()
            except Exception:
                logger.exception("Order execution failed")
                self.last_decision_reason = "entry_execution_exception"

        except Exception as e:
            logger.error("Iteration failed: %s", e, exc_info=True)
            self.last_decision_reason = "iteration_exception"

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

        sync_fetch_ohlcv_fn = getattr(data_service, "_fetch_kraken_ohlcv_sync", None)
        if callable(sync_fetch_ohlcv_fn):
            return await asyncio.to_thread(sync_fetch_ohlcv_fn, symbol, timeframe, limit)

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
        local_symbol = self._find_local_position_symbol(symbol) or symbol
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
            if local_symbol in self.risk_manager.positions:
                pos = self.risk_manager.positions.get(local_symbol) or {}
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

                    self.risk_manager.close_position(local_symbol, exit_price)
                    self.position_guards.pop(local_symbol, None)

                    trade_record = {
                        "timestamp": (result or {}).get("timestamp") or signal.get("timestamp"),
                        "symbol": local_symbol,
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
                self.risk_manager.open_position(local_symbol, side, signal.get("quantity", 0), avg_fill_price)
                self._ensure_position_guard(local_symbol, side, avg_fill_price)
                entry_price = avg_fill_price
                outcome = "open"

        order_record = {
            "symbol": local_symbol,
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
            "last_decision_reason": self.last_decision_reason,
            "last_entry_gate_snapshot": self.last_entry_gate_snapshot,
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
