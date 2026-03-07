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
from enum import Enum
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
from app.monitoring.alert_manager import AlertManager
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


class ExitReason(str, Enum):
    CONFIDENCE_FLOOR = "gate_confidence_floor"
    COMPOSITE_FLOOR = "gate_composite_floor"
    TREND_FLOOR = "gate_trend_floor"
    PATTERN_FLOOR = "gate_pattern_floor"
    IMBALANCE_FLOOR = "gate_imbalance_floor"
    OPPOSITE_SPIKE = "gate_opposite_imbalance_spike"
    PROFIT_LOCK = "profit_lock_stop"
    STRONG_REVERSAL = "strong_reversal"
    TIME_STOP = "time_stop"
    VOLATILITY_CONTRACT = "volatility_contraction"
    LIQUIDITY_VACUUM = "liquidity_vacuum"
    CORRELATION_SPIKE = "correlation_spike"
    LOSS_CUT = "loss_cut"
    LEVERAGE_BREACH = "leverage_breach"
    CONTRACT_INFLATION = "contract_inflation"
    MANUAL_UI = "manual_ui_trigger"
    MAX_DRAWDOWN = "stopped_max_drawdown"


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
            sandbox_mode = self._sandbox_mode_from_env(True)
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
        execution_sandbox = bool(getattr(self.execution_engine, "sandbox", self._sandbox_mode_from_env(True)))
        demo_relaxed_entry_gates = self._env_bool("MOMENTUM_DEMO_RELAXED_ENTRY_GATES", execution_sandbox)
        default_conf_gate = "20.0" if demo_relaxed_entry_gates else "55.0"
        default_conviction_gate = "0.12" if demo_relaxed_entry_gates else "0.35"
        default_agreement_gate = "0.20" if demo_relaxed_entry_gates else "0.30"
        default_trend_gate = "0.20" if demo_relaxed_entry_gates else "0.30"
        default_pattern_gate = "0.20" if demo_relaxed_entry_gates else "0.25"
        entry_conf_gate_raw = os.environ.get("MOMENTUM_ENTRY_CONF_GATE_PCT")
        if entry_conf_gate_raw is None:
            entry_conf_gate_raw = os.environ.get("MOMENTUM_CONFIDENCE_GATE", default_conf_gate)
        try:
            self.entry_confidence_gate_pct = float(entry_conf_gate_raw or default_conf_gate)
        except (TypeError, ValueError):
            self.entry_confidence_gate_pct = float(default_conf_gate)
        self.entry_confidence_gate_pct = max(0.0, min(self.entry_confidence_gate_pct, 100.0))
        try:
            self.entry_conviction_gate = float(
                os.environ.get("MOMENTUM_ENTRY_CONVICTION_GATE", default_conviction_gate) or default_conviction_gate
            )
        except (TypeError, ValueError):
            self.entry_conviction_gate = float(default_conviction_gate)
        self.entry_conviction_gate = max(0.0, min(self.entry_conviction_gate, 1.0))
        try:
            self.entry_agreement_gate = float(
                os.environ.get("MOMENTUM_ENTRY_AGREEMENT_GATE", default_agreement_gate) or default_agreement_gate
            )
        except (TypeError, ValueError):
            self.entry_agreement_gate = float(default_agreement_gate)
        self.entry_agreement_gate = max(0.0, min(self.entry_agreement_gate, 1.0))
        try:
            self.entry_trend_gate = float(
                os.environ.get("MOMENTUM_ENTRY_TREND_GATE", default_trend_gate) or default_trend_gate
            )
        except (TypeError, ValueError):
            self.entry_trend_gate = float(default_trend_gate)
        self.entry_trend_gate = max(0.0, min(self.entry_trend_gate, 1.0))
        try:
            self.entry_pattern_gate = float(
                os.environ.get("MOMENTUM_ENTRY_PATTERN_GATE", default_pattern_gate) or default_pattern_gate
            )
        except (TypeError, ValueError):
            self.entry_pattern_gate = float(default_pattern_gate)
        self.entry_pattern_gate = max(0.0, min(self.entry_pattern_gate, 1.0))
        self.entry_trend_adaptive = self._env_bool("MOMENTUM_ENTRY_TREND_ADAPTIVE", True)
        try:
            self.entry_trend_strong_threshold = float(
                os.environ.get("MOMENTUM_ENTRY_TREND_STRONG_THRESHOLD", "0.60") or "0.60"
            )
        except (TypeError, ValueError):
            self.entry_trend_strong_threshold = 0.60
        self.entry_trend_strong_threshold = max(0.0, min(self.entry_trend_strong_threshold, 1.0))
        try:
            self.entry_trend_weak_threshold = float(
                os.environ.get("MOMENTUM_ENTRY_TREND_WEAK_THRESHOLD", "0.20") or "0.20"
            )
        except (TypeError, ValueError):
            self.entry_trend_weak_threshold = 0.20
        self.entry_trend_weak_threshold = max(0.0, min(self.entry_trend_weak_threshold, 1.0))
        try:
            self.entry_trend_relax_multiplier = float(
                os.environ.get("MOMENTUM_ENTRY_TREND_RELAX_MULTIPLIER", "0.75") or "0.75"
            )
        except (TypeError, ValueError):
            self.entry_trend_relax_multiplier = 0.75
        self.entry_trend_relax_multiplier = max(0.50, min(self.entry_trend_relax_multiplier, 1.0))
        try:
            self.entry_trend_strict_multiplier = float(
                os.environ.get("MOMENTUM_ENTRY_TREND_STRICT_MULTIPLIER", "1.25") or "1.25"
            )
        except (TypeError, ValueError):
            self.entry_trend_strict_multiplier = 1.25
        self.entry_trend_strict_multiplier = max(1.0, min(self.entry_trend_strict_multiplier, 2.0))
        self.entry_block_countertrend = self._env_bool("MOMENTUM_ENTRY_BLOCK_COUNTERTREND", True)
        self.entry_auto_size_enabled = self._env_bool("MOMENTUM_ENTRY_AUTO_SIZE_ENABLED", True)
        self.entry_auto_size_override_signal_qty = self._env_bool("MOMENTUM_ENTRY_AUTO_SIZE_OVERRIDE_SIGNAL_QTY", True)
        try:
            self.entry_auto_size_target_leverage = float(
                os.environ.get("MOMENTUM_ENTRY_AUTO_SIZE_TARGET_LEVERAGE", "0.50") or "0.50"
            )
        except (TypeError, ValueError):
            self.entry_auto_size_target_leverage = 0.50
        self.entry_auto_size_target_leverage = max(0.01, min(self.entry_auto_size_target_leverage, 10.0))
        try:
            self.entry_auto_size_leverage_cap_fraction = float(
                os.environ.get("MOMENTUM_ENTRY_AUTO_SIZE_LEVERAGE_CAP_FRACTION", "0.90") or "0.90"
            )
        except (TypeError, ValueError):
            self.entry_auto_size_leverage_cap_fraction = 0.90
        self.entry_auto_size_leverage_cap_fraction = max(0.10, min(self.entry_auto_size_leverage_cap_fraction, 1.0))
        try:
            self.entry_auto_size_min_qty = float(os.environ.get("MOMENTUM_ENTRY_AUTO_SIZE_MIN_QTY", "0.0001") or "0.0001")
        except (TypeError, ValueError):
            self.entry_auto_size_min_qty = 0.0001
        self.entry_auto_size_min_qty = max(0.0, self.entry_auto_size_min_qty)
        try:
            self.entry_auto_size_max_qty = float(os.environ.get("MOMENTUM_ENTRY_AUTO_SIZE_MAX_QTY", "0.05") or "0.05")
        except (TypeError, ValueError):
            self.entry_auto_size_max_qty = 0.05
        self.entry_auto_size_max_qty = max(self.entry_auto_size_min_qty, self.entry_auto_size_max_qty)
        logger.info(
            "Entry gates configured | conf>=%.2f conviction>=%.2f trend>=%.2f pattern>=%.2f agreement>=%.2f demo_relaxed=%s",
            self.entry_confidence_gate_pct,
            self.entry_conviction_gate,
            self.entry_trend_gate,
            self.entry_pattern_gate,
            self.entry_agreement_gate,
            demo_relaxed_entry_gates,
        )
        logger.info(
            "Entry trend-adaptive gates | enabled=%s strong>=%.2f weak<=%.2f relax=%.2f strict=%.2f block_countertrend=%s",
            self.entry_trend_adaptive,
            self.entry_trend_strong_threshold,
            self.entry_trend_weak_threshold,
            self.entry_trend_relax_multiplier,
            self.entry_trend_strict_multiplier,
            self.entry_block_countertrend,
        )
        logger.info(
            "Entry auto-size | enabled=%s override_qty=%s target_lev=%.2f cap_frac=%.2f min_qty=%s max_qty=%s",
            self.entry_auto_size_enabled,
            self.entry_auto_size_override_signal_qty,
            self.entry_auto_size_target_leverage,
            self.entry_auto_size_leverage_cap_fraction,
            self.entry_auto_size_min_qty,
            self.entry_auto_size_max_qty,
        )
        # --- Partial fill reconciliation ---
        self.reconcile_partial_fills = self._env_bool("MOMENTUM_RECONCILE_PARTIAL_FILLS", True)
        try:
            self.partial_fill_poll_sec = float(os.environ.get("MOMENTUM_PARTIAL_FILL_POLL_SEC", "30") or "30")
        except (TypeError, ValueError):
            self.partial_fill_poll_sec = 30.0
        self.partial_fill_poll_sec = max(5.0, self.partial_fill_poll_sec)
        self._pending_fill_orders: dict[str, dict[str, Any]] = {}  # order_id -> {symbol, side, expected_qty, filled_qty, placed_at}

        # --- Watchdog / stale restart ---
        self.watchdog_enabled = self._env_bool("MOMENTUM_WATCHDOG_ENABLED", True)
        try:
            self.watchdog_max_stale_sec = float(os.environ.get("MOMENTUM_WATCHDOG_MAX_STALE_SEC", "300") or "300")
        except (TypeError, ValueError):
            self.watchdog_max_stale_sec = 300.0
        self.watchdog_max_stale_sec = max(60.0, self.watchdog_max_stale_sec)
        self._last_iteration_ts: float = 0.0
        self._watchdog_restart_count: int = 0

        # --- Multi-timeframe HTF confirmation ---
        self.htf_enabled = self._env_bool("MOMENTUM_HTF_ENABLED", True)
        self.htf_timeframe = os.environ.get("MOMENTUM_HTF_TIMEFRAME", "1h") or "1h"
        try:
            self.htf_trend_agreement_weight = float(
                os.environ.get("MOMENTUM_HTF_TREND_WEIGHT", "0.30") or "0.30"
            )
        except (TypeError, ValueError):
            self.htf_trend_agreement_weight = 0.30
        self.htf_trend_agreement_weight = max(0.0, min(self.htf_trend_agreement_weight, 1.0))
        self._htf_cache: dict[str, Any] = {}  # cached HTF context
        self._htf_cache_ts: float = 0.0
        try:
            self._htf_cache_ttl_sec = float(os.environ.get("MOMENTUM_HTF_CACHE_TTL_SEC", "300") or "300")
        except (TypeError, ValueError):
            self._htf_cache_ttl_sec = 300.0

        # --- Volume confirmation gate ---
        self.volume_gate_enabled = self._env_bool("MOMENTUM_VOLUME_GATE_ENABLED", True)
        try:
            self.volume_gate_min_ratio = float(
                os.environ.get("MOMENTUM_VOLUME_GATE_MIN_RATIO", "0.50") or "0.50"
            )
        except (TypeError, ValueError):
            self.volume_gate_min_ratio = 0.50
        self.volume_gate_min_ratio = max(0.0, self.volume_gate_min_ratio)

        # --- Native exchange stop loss ---
        self.native_stop_enabled = self._env_bool("MOMENTUM_NATIVE_STOP_ENABLED", True)
        try:
            self.native_stop_loss_pct = float(
                os.environ.get("MOMENTUM_NATIVE_STOP_LOSS_PCT", "0.02") or "0.02"
            )
        except (TypeError, ValueError):
            self.native_stop_loss_pct = 0.02
        self.native_stop_loss_pct = max(0.001, min(self.native_stop_loss_pct, 0.20))
        self._active_stop_orders: dict[str, str] = {}  # symbol -> stop_order_id

        logger.info(
            "Extended gates | partial_fills=%s watchdog=%s(%.0fs) htf=%s(%s) volume_gate=%s(%.2f) native_stop=%s(%.2f%%)",
            self.reconcile_partial_fills,
            self.watchdog_enabled, self.watchdog_max_stale_sec,
            self.htf_enabled, self.htf_timeframe,
            self.volume_gate_enabled, self.volume_gate_min_ratio,
            self.native_stop_enabled, self.native_stop_loss_pct * 100,
        )

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
        self.enforce_exit_execution_gates = self._env_bool("MOMENTUM_ENFORCE_EXIT_GATES", True)
        self.exit_confidence_floor_pct = float(
            os.environ.get("MOMENTUM_EXIT_CONFIDENCE_FLOOR_PCT", "50.0") or "50.0"
        )
        self.exit_confidence_floor_pct = max(0.0, min(self.exit_confidence_floor_pct, 100.0))
        self.exit_composite_floor = float(os.environ.get("MOMENTUM_EXIT_COMPOSITE_FLOOR", "0.15") or "0.15")
        self.exit_composite_floor = max(0.0, min(self.exit_composite_floor, 1.0))
        self.exit_trend_floor = float(os.environ.get("MOMENTUM_EXIT_TREND_FLOOR", "0.10") or "0.10")
        self.exit_trend_floor = max(0.0, min(self.exit_trend_floor, 1.0))
        self.exit_pattern_floor = float(os.environ.get("MOMENTUM_EXIT_PATTERN_FLOOR", "0.10") or "0.10")
        self.exit_pattern_floor = max(0.0, min(self.exit_pattern_floor, 1.0))
        self.exit_imbalance_floor = float(os.environ.get("MOMENTUM_EXIT_IMBALANCE_FLOOR", "0.10") or "0.10")
        self.exit_imbalance_floor = max(0.0, min(self.exit_imbalance_floor, 1.0))
        self.exit_opposite_imbalance_spike = float(
            os.environ.get("MOMENTUM_EXIT_OPPOSITE_IMBALANCE_SPIKE", "0.25") or "0.25"
        )
        self.exit_opposite_imbalance_spike = max(0.0, min(self.exit_opposite_imbalance_spike, 1.0))
        self.gate_fail_counts: dict[str, int] = {
            "confidence": 0,
            "composite": 0,
            "trend": 0,
            "pattern": 0,
            "imbalance": 0,
            "opposite_spike": 0,
            "profit_lock": 0,
            "strong_reversal": 0,
            "time_stop": 0,
            "volatility_contract": 0,
            "liquidity_vacuum": 0,
            "correlation_spike": 0,
            "loss_cut": 0,
        }
        self.gate_fail_thresholds: dict[str, int] = {
            "confidence": self._env_int("HYSTERESIS_CONFIDENCE", 2, minimum=1),
            "composite": self._env_int("HYSTERESIS_COMPOSITE", 2, minimum=1),
            "trend": self._env_int("HYSTERESIS_TREND", 2, minimum=1),
            "pattern": self._env_int("HYSTERESIS_PATTERN", 2, minimum=1),
            "imbalance": self._env_int("HYSTERESIS_IMBALANCE", 2, minimum=1),
            "opposite_spike": self._env_int("HYSTERESIS_OPPOSITE_SPIKE", 1, minimum=1),
            "profit_lock": self._env_int("HYSTERESIS_PROFIT_LOCK", 1, minimum=1),
            "strong_reversal": self._env_int("HYSTERESIS_STRONG_REVERSAL", 2, minimum=1),
            "time_stop": self._env_int("HYSTERESIS_TIME_STOP", 1, minimum=1),
            "volatility_contract": self._env_int("HYSTERESIS_VOL_CONTRACT", 3, minimum=1),
            "liquidity_vacuum": self._env_int("HYSTERESIS_LIQUIDITY", 3, minimum=1),
            "correlation_spike": self._env_int("HYSTERESIS_CORRELATION", 3, minimum=1),
            "loss_cut": self._env_int("HYSTERESIS_LOSS_CUT", 1, minimum=1),
        }

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
        try:
            setattr(self.execution_engine, "risk_manager", self.risk_manager)
        except Exception:
            pass

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
        self.last_exit_gate_snapshot: dict[str, Any] = {}
        self._trend_score_history: deque[float] = deque(maxlen=50)
        self.exit_history: deque[dict[str, Any]] = deque(maxlen=2000)

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

    @classmethod
    def _sandbox_mode_from_env(cls, default: bool = True) -> bool:
        raw = os.environ.get("KRAKEN_SANDBOX")
        if raw is not None:
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return cls._env_bool("KRAKEN_FUTURES_DEMO", default)

    @staticmethod
    def _env_int(name: str, default: int, minimum: int = 0) -> int:
        raw = os.environ.get(name)
        try:
            value = int(raw) if raw is not None else int(default)
        except (TypeError, ValueError):
            value = int(default)
        return max(int(minimum), value)

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
        # Signed version preserves direction for composite calculation
        trend_score_directional = trend_score if trend_raw >= 0 else -abs(trend_score)
        ret_10 = float((close.iloc[-1] / close.iloc[-11] - 1.0) * 100.0) if len(close) >= 11 else 0.0
        momentum_score = self._clip_score(ret_10 / 0.60)
        # Signed version preserves direction for composite calculation
        momentum_score_directional = momentum_score if ret_10 >= 0 else -abs(momentum_score)

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

        # Neutralise confidence contribution when at floor (no real signal)
        _conf_contribution = (
            self._clip_score((confidence_pct - 50.0) / 25.0)
            if confidence_pct > 5.5
            else 0.0
        )

        composite = (
            0.25 * trend_score_directional
            + 0.20 * momentum_score_directional
            + 0.15 * vol_score
            + 0.10 * (pattern_score if side == "buy" else -pattern_score)
            + 0.20 * _conf_contribution
            + 0.10 * self._clip_score(agreement_raw)
        )

        side_sign = 1.0 if side == "buy" else -1.0
        aligned_trend_score = side_sign * trend_score_directional
        trend_regime = "aligned_normal"
        if aligned_trend_score < 0.0:
            trend_regime = "misaligned"
        elif aligned_trend_score >= self.entry_trend_strong_threshold:
            trend_regime = "aligned_strong"
        elif aligned_trend_score <= self.entry_trend_weak_threshold:
            trend_regime = "aligned_weak"

        threshold_mult = 1.0
        if self.entry_trend_adaptive:
            if trend_regime == "aligned_strong":
                threshold_mult = self.entry_trend_relax_multiplier
            elif trend_regime in {"aligned_weak", "misaligned"}:
                threshold_mult = self.entry_trend_strict_multiplier

        effective_confidence_gate_pct = max(0.0, min(100.0, self.entry_confidence_gate_pct * threshold_mult))
        effective_conviction_gate = max(0.0, min(1.0, self.entry_conviction_gate * threshold_mult))
        effective_pattern_gate = max(0.0, min(1.0, self.entry_pattern_gate * threshold_mult))
        effective_agreement_gate = max(0.0, min(1.0, self.entry_agreement_gate * threshold_mult))

        # Record absolute trend strength for rolling percentile thresholding.
        self._trend_score_history.append(abs(trend_score))
        if vol_ann > 0.60:
            _trend_regime = "high_vol"
        elif vol_ann > 0.30:
            _trend_regime = "trending"
        else:
            _trend_regime = "ranging"
        _regime_base = {"high_vol": 0.10, "trending": 0.20, "ranging": 0.30}[_trend_regime]
        if len(self._trend_score_history) >= 10:
            _sorted = sorted(self._trend_score_history)
            _p50 = _sorted[len(_sorted) // 2]
            _dynamic_threshold = min(_regime_base, max(0.10, _p50 * 0.80))
        else:
            _dynamic_threshold = _regime_base

        confidence_gate = confidence_pct >= effective_confidence_gate_pct
        conviction_gate = abs(composite) >= effective_conviction_gate
        trend_gate = abs(trend_score) >= _dynamic_threshold
        vol_gate = vol_score >= 0.0
        pattern_gate = pattern_score >= effective_pattern_gate
        # Waive agreement gate until signal history is established.
        # On tick 1 signal_history is empty so agreement is always 0.
        _min_history_for_agreement = 10
        agreement_gate = (
            len(self.signal_history) < _min_history_for_agreement
            or agreement_score >= effective_agreement_gate
        )

        countertrend_blocked = bool(self.entry_block_countertrend and aligned_trend_score < 0.0)
        if side == "buy":
            direction_gate = composite >= effective_conviction_gate
        else:
            direction_gate = composite <= -effective_conviction_gate
        if countertrend_blocked:
            direction_gate = False

        # Volume confirmation gate
        volume_gate_ok, volume_ratio = self._volume_gate_check(context)

        allowed = (
            confidence_gate
            and conviction_gate
            and trend_gate
            and vol_gate
            and pattern_gate
            and agreement_gate
            and direction_gate
            and volume_gate_ok
        )

        # Candlestick patterns detected
        candle_patterns_list = context.get("candle_patterns", [])

        snapshot: dict[str, Any] = {
            "side": side,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confidence_pct": confidence_pct,
            "composite": float(composite),
            "trend_score": float(trend_score),
            "trend_score_directional": float(trend_score_directional),
            "momentum_score_directional": float(momentum_score_directional),
            "vol_score": float(vol_score),
            "pattern_score": float(pattern_score),
            "candle_patterns": candle_patterns_list,
            "volume_ratio": float(volume_ratio),
            "volume_gate": bool(volume_gate_ok),
            "trend_regime": _trend_regime,
            "aligned_trend_regime": trend_regime,
            "aligned_trend_score": float(aligned_trend_score),
            "countertrend_blocked": bool(countertrend_blocked),
            "gate_threshold_multiplier": float(threshold_mult),
            "trend_gate_threshold": float(self.entry_trend_gate),
            "trend_threshold": round(float(_dynamic_threshold), 4),
            "effective_confidence_gate_pct": float(effective_confidence_gate_pct),
            "effective_conviction_gate": float(effective_conviction_gate),
            "effective_trend_gate": float(_dynamic_threshold),
            "effective_pattern_gate": float(effective_pattern_gate),
            "effective_agreement_gate": float(effective_agreement_gate),
            "pattern_gate_threshold": float(self.entry_pattern_gate),
            "agreement_raw": float(agreement_raw),
            "agreement_score": float(agreement_score),
            "signal_history_len": len(self.signal_history),
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
        if not volume_gate_ok:
            return False, "entry_gate_failed:volume_too_low", snapshot
        if not pattern_gate:
            return False, "entry_gate_failed:pattern", snapshot
        if not agreement_gate:
            return False, "entry_gate_failed:agreement", snapshot
        if countertrend_blocked:
            return False, "entry_gate_failed:countertrend", snapshot
        if not direction_gate:
            return False, "entry_gate_failed:direction", snapshot
        return False, "entry_gate_failed:unknown", snapshot

    # ------------------------------------------------------------------
    # (1) Partial fill reconciliation
    # ------------------------------------------------------------------

    def _track_pending_order(self, order_id: str, symbol: str, side: str, expected_qty: float) -> None:
        """Register a submitted order for partial fill tracking."""
        if not self.reconcile_partial_fills or not order_id:
            return
        self._pending_fill_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "expected_qty": float(expected_qty),
            "filled_qty": 0.0,
            "placed_at": datetime.now(timezone.utc).timestamp(),
        }

    async def _reconcile_partial_fills(self) -> None:
        """Poll exchange for partially filled maker orders and reconcile position qty."""
        if not self.reconcile_partial_fills or not self._pending_fill_orders:
            return
        exchange = getattr(self.execution_engine, "exchange", None)
        if exchange is None or getattr(self.execution_engine, "paper_mode", True):
            return

        completed: list[str] = []
        for order_id, info in list(self._pending_fill_orders.items()):
            try:
                symbol = str(info.get("symbol", self.symbol))
                exchange_symbol = self.execution_engine._resolve_exchange_symbol(symbol)
                fetched = await asyncio.to_thread(exchange.fetch_order, order_id, exchange_symbol)
                if not fetched:
                    continue

                filled = float(fetched.get("filled") or 0.0)
                status = str(fetched.get("status") or "").lower()
                prev_filled = float(info.get("filled_qty", 0.0))

                if filled > prev_filled:
                    delta = filled - prev_filled
                    info["filled_qty"] = filled
                    logger.info(
                        "Partial fill reconciled | order=%s filled=%.6f/%.6f delta=%.6f status=%s",
                        order_id, filled, info["expected_qty"], delta, status,
                    )
                    # Update position quantity if already tracked
                    local_symbol = self._find_local_position_symbol(symbol) or symbol
                    pos = self.risk_manager.positions.get(local_symbol)
                    if pos and prev_filled > 0:
                        pos["quantity"] = float(filled)

                if status in ("closed", "filled", "canceled", "cancelled", "expired", "rejected"):
                    completed.append(order_id)
                    final_filled = float(info.get("filled_qty", 0.0))
                    expected = float(info["expected_qty"])
                    if final_filled < expected and final_filled > 0:
                        logger.warning(
                            "Order partially filled | order=%s filled=%.6f expected=%.6f status=%s",
                            order_id, final_filled, expected, status,
                        )
                        local_symbol = self._find_local_position_symbol(symbol) or symbol
                        pos = self.risk_manager.positions.get(local_symbol)
                        if pos:
                            pos["quantity"] = float(final_filled)
                    elif final_filled <= 0 and status in ("canceled", "cancelled", "expired"):
                        # Never filled — remove position if we opened one
                        local_symbol = self._find_local_position_symbol(symbol) or symbol
                        if local_symbol in self.risk_manager.positions:
                            side = str(info.get("side", "")).lower()
                            pos_side = str(self.risk_manager.positions[local_symbol].get("side", "")).lower()
                            if side == pos_side:
                                del self.risk_manager.positions[local_symbol]
                                self.position_guards.pop(local_symbol, None)
                                logger.info("Removed unfilled position | symbol=%s order=%s", local_symbol, order_id)

            except Exception as exc:
                logger.debug("Failed polling order %s: %s", order_id, exc)

        for oid in completed:
            self._pending_fill_orders.pop(oid, None)

    # ------------------------------------------------------------------
    # (2) Real candlestick pattern detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_candlestick_patterns(candles: pd.DataFrame) -> dict[str, float]:
        """Detect real candlestick patterns and return bullish/bearish scores [0..1]."""
        result = {"bullish": 0.0, "bearish": 0.0, "patterns": []}
        if candles is None or len(candles) < 3:
            return result

        o = pd.to_numeric(candles["open"], errors="coerce")
        h = pd.to_numeric(candles["high"], errors="coerce")
        low = pd.to_numeric(candles["low"], errors="coerce")
        c = pd.to_numeric(candles["close"], errors="coerce")

        # Last 3 candles
        o1, o2, o3 = float(o.iloc[-1]), float(o.iloc[-2]), float(o.iloc[-3])
        h1, h2, h3 = float(h.iloc[-1]), float(h.iloc[-2]), float(h.iloc[-3])
        l1, l2, l3 = float(low.iloc[-1]), float(low.iloc[-2]), float(low.iloc[-3])
        c1, c2, c3 = float(c.iloc[-1]), float(c.iloc[-2]), float(c.iloc[-3])

        body1 = abs(c1 - o1)
        body2 = abs(c2 - o2)
        range1 = max(h1 - l1, 1e-9)
        range2 = max(h2 - l2, 1e-9)

        upper_wick1 = h1 - max(o1, c1)
        lower_wick1 = min(o1, c1) - l1

        bullish_score = 0.0
        bearish_score = 0.0
        patterns = []

        # Bullish engulfing
        if c2 < o2 and c1 > o1 and o1 <= c2 and c1 >= o2:
            bullish_score += 0.4
            patterns.append("bullish_engulfing")

        # Bearish engulfing
        if c2 > o2 and c1 < o1 and o1 >= c2 and c1 <= o2:
            bearish_score += 0.4
            patterns.append("bearish_engulfing")

        # Hammer (bullish): small body at top, long lower wick
        if lower_wick1 >= body1 * 2.0 and upper_wick1 < body1 * 0.5 and body1 / range1 < 0.35:
            bullish_score += 0.3
            patterns.append("hammer")

        # Shooting star (bearish): small body at bottom, long upper wick
        if upper_wick1 >= body1 * 2.0 and lower_wick1 < body1 * 0.5 and body1 / range1 < 0.35:
            bearish_score += 0.3
            patterns.append("shooting_star")

        # Doji (indecision)
        if body1 / range1 < 0.10:
            patterns.append("doji")

        # Morning star (bullish 3-bar)
        if (c3 < o3 and body2 / range2 < 0.20 and c1 > o1
                and c1 > (o3 + c3) / 2):
            bullish_score += 0.3
            patterns.append("morning_star")

        # Evening star (bearish 3-bar)
        if (c3 > o3 and body2 / range2 < 0.20 and c1 < o1
                and c1 < (o3 + c3) / 2):
            bearish_score += 0.3
            patterns.append("evening_star")

        # Three white soldiers (bullish)
        if c1 > o1 and c2 > o2 and c3 > o3 and c1 > c2 > c3:
            bullish_score += 0.25
            patterns.append("three_white_soldiers")

        # Three black crows (bearish)
        if c1 < o1 and c2 < o2 and c3 < o3 and c1 < c2 < c3:
            bearish_score += 0.25
            patterns.append("three_black_crows")

        result["bullish"] = min(1.0, bullish_score)
        result["bearish"] = min(1.0, bearish_score)
        result["patterns"] = patterns
        return result

    # ------------------------------------------------------------------
    # (3) Volume confirmation gate
    # ------------------------------------------------------------------

    def _volume_gate_check(self, context: dict[str, Any]) -> tuple[bool, float]:
        """Return (passes, volume_ratio) - blocks entry if volume too low."""
        if not self.volume_gate_enabled:
            return True, 1.0
        vol_ma20 = float(context.get("vol_ma20", 0.0))
        vol_last = float(context.get("vol_last", 0.0))
        if vol_ma20 <= 0:
            return True, 1.0  # Can't evaluate, pass
        ratio = vol_last / vol_ma20
        return ratio >= self.volume_gate_min_ratio, round(ratio, 4)

    # ------------------------------------------------------------------
    # (4) Multi-timeframe HTF confirmation
    # ------------------------------------------------------------------

    async def _fetch_htf_context(self) -> dict[str, Any]:
        """Fetch and cache higher-timeframe context metrics."""
        import time as _time
        now = _time.time()
        if self._htf_cache and (now - self._htf_cache_ts) < self._htf_cache_ttl_sec:
            return self._htf_cache

        try:
            htf_candles = await self._load_ohlcv(
                symbol=self.symbol,
                timeframe=self.htf_timeframe,
                limit=50,
            )
            if htf_candles is not None and not htf_candles.empty:
                ctx = self._compute_context_metrics(htf_candles)
                if ctx:
                    self._htf_cache = ctx
                    self._htf_cache_ts = now
                    return ctx
        except Exception as exc:
            logger.debug("HTF fetch failed (%s): %s", self.htf_timeframe, exc)

        return self._htf_cache or {}

    def _htf_trend_agrees(self, side: str, htf_context: dict[str, Any]) -> tuple[bool, float]:
        """Check if higher-timeframe trend agrees with proposed entry side."""
        if not self.htf_enabled or not htf_context:
            return True, 0.0
        htf_trend = float(htf_context.get("trend_pct", 0.0))
        if side == "buy":
            agrees = htf_trend >= 0
        else:
            agrees = htf_trend <= 0
        return agrees, round(htf_trend, 4)

    # ------------------------------------------------------------------
    # (5) Native exchange stop loss
    # ------------------------------------------------------------------

    async def _place_native_stop(self, symbol: str, side: str, entry_price: float, qty: float) -> str | None:
        """Place a native stop-market order on the exchange after entry."""
        if not self.native_stop_enabled:
            return None
        exchange = getattr(self.execution_engine, "exchange", None)
        if exchange is None or getattr(self.execution_engine, "paper_mode", True):
            return None

        try:
            exchange_symbol = self.execution_engine._resolve_exchange_symbol(symbol)
            stop_side = "sell" if side == "buy" else "buy"
            if side == "buy":
                stop_price = entry_price * (1.0 - self.native_stop_loss_pct)
            else:
                stop_price = entry_price * (1.0 + self.native_stop_loss_pct)

            # Normalize precision
            try:
                stop_price = float(exchange.price_to_precision(exchange_symbol, stop_price))
            except Exception:
                pass

            # Use exchange amount for contracts
            market = exchange.market(exchange_symbol)
            amount = qty
            if market.get("contract"):
                contract_size = float(market.get("contractSize") or 1.0) or 1.0
                inverse = bool(market.get("inverse"))
                if inverse:
                    amount = max(1, round(qty * entry_price / contract_size))
                else:
                    amount = max(1, round(qty / contract_size))
            try:
                amount = float(exchange.amount_to_precision(exchange_symbol, amount))
            except Exception:
                pass

            params: dict[str, Any] = {"reduceOnly": True}
            # Kraken futures uses stopPrice in params
            params["stopPrice"] = stop_price
            params["triggerPrice"] = stop_price

            order = await asyncio.to_thread(
                exchange.create_order,
                exchange_symbol,
                "stop",
                stop_side,
                amount,
                None,
                params,
            )
            order_id = str(order.get("id") or "")
            self._active_stop_orders[symbol] = order_id
            logger.info(
                "Native stop placed | symbol=%s side=%s stop_price=%.2f qty=%.6f order_id=%s",
                exchange_symbol, stop_side, stop_price, amount, order_id,
            )
            try:
                AlertManager.instance().send(
                    "info", "Stop Loss Placed",
                    f"Native stop {stop_side.upper()} @ {stop_price:,.2f} for {symbol}",
                    {"stop_order_id": order_id, "stop_price": f"{stop_price:,.2f}"},
                )
            except Exception:
                pass
            return order_id

        except Exception as exc:
            logger.warning("Failed to place native stop for %s: %s", symbol, exc)
            return None

    async def _cancel_native_stop(self, symbol: str) -> None:
        """Cancel the native stop order when position is closed."""
        order_id = self._active_stop_orders.pop(symbol, None)
        if not order_id:
            return
        exchange = getattr(self.execution_engine, "exchange", None)
        if exchange is None:
            return
        try:
            exchange_symbol = self.execution_engine._resolve_exchange_symbol(symbol)
            await asyncio.to_thread(exchange.cancel_order, order_id, exchange_symbol)
            logger.info("Native stop canceled | symbol=%s order_id=%s", symbol, order_id)
        except Exception as exc:
            logger.debug("Failed to cancel native stop %s: %s", order_id, exc)

    # ------------------------------------------------------------------
    # (6) Watchdog heartbeat
    # ------------------------------------------------------------------

    def _record_heartbeat(self) -> None:
        """Record that an iteration completed successfully."""
        import time as _time
        self._last_iteration_ts = _time.time()

    def _is_stale(self) -> bool:
        """Check if worker hasn't completed an iteration within threshold."""
        if not self.watchdog_enabled or self._last_iteration_ts <= 0:
            return False
        import time as _time
        elapsed = _time.time() - self._last_iteration_ts
        return elapsed > self.watchdog_max_stale_sec

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

        trend_magnitude = abs(trend_pct)
        momentum_magnitude = abs(momentum)
        direction_agreement = 1.0 if (trend_pct * momentum >= 0) else 0.7

        # Blend: weighted trend/momentum magnitudes + candle body + bar ratios
        body_strength = max(bullish_body, bearish_body)
        bar_bias = max(up_ratio, down_ratio)
        confidence_score = min(
            99.0,
            max(
                5.0,
                (
                    trend_magnitude * 25.0
                    + momentum_magnitude * 30.0
                    + body_strength * 15.0
                    + bar_bias * 10.0
                ) * direction_agreement,
            ),
        )
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
        vol_ratio = (vol_last / vol_ma20) if vol_ma20 > 0 else 1.0
        correlation = self._correlation_spike(candles)

        # Real candlestick pattern detection
        candle_patterns = self._detect_candlestick_patterns(candles)
        # Blend body-ratio pattern score with real pattern detection (50/50)
        pattern_long_blended = 0.5 * bullish_body + 0.5 * float(candle_patterns.get("bullish", 0.0))
        pattern_short_blended = 0.5 * bearish_body + 0.5 * float(candle_patterns.get("bearish", 0.0))

        return {
            "price": current_price,
            "sma20": sma20,
            "sma50": sma50,
            "trend_score": float(trend_score),
            "trend_pct": float(trend_pct),
            "momentum": float(momentum),
            "confidence": float(confidence_score),
            "pattern_long": float(pattern_long_blended),
            "pattern_short": float(pattern_short_blended),
            "pattern_long_body": float(bullish_body),
            "pattern_short_body": float(bearish_body),
            "pattern_long_candle": float(candle_patterns.get("bullish", 0.0)),
            "pattern_short_candle": float(candle_patterns.get("bearish", 0.0)),
            "candle_patterns": candle_patterns.get("patterns", []),
            "imbalance_long": float(up_ratio),
            "imbalance_short": float(down_ratio),
            "composite_long": float(composite_long),
            "composite_short": float(composite_short),
            "atr": float(atr),
            "vol_ma20": float(vol_ma20),
            "vol_last": float(vol_last),
            "vol_ratio": float(vol_ratio),
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

    def _reason_with_side(self, reason: ExitReason | str, is_long: bool) -> str:
        base = str(reason.value if isinstance(reason, ExitReason) else reason)
        return base if is_long else f"{base}_short"

    def _get_loss_cut_label(self, is_long: bool = True) -> str:
        pct = max(0.0, float(self.exit_max_loss_pct) * 100.0)
        pct_text = f"{pct:.4f}".rstrip("0").rstrip(".")
        base = f"{ExitReason.LOSS_CUT.value}_{pct_text}pct"
        return base if is_long else f"{base}_short"

    def _record_exit_event(
        self,
        *,
        symbol: str,
        position_side: str,
        reason: str,
        snapshot: dict[str, Any],
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": str(symbol),
            "position_side": str(position_side),
            "reason": str(reason),
            "snapshot": dict(snapshot),
        }
        self.exit_history.append(event)

    def _reset_hysteresis(self) -> None:
        for key in list(self.gate_fail_counts.keys()):
            self.gate_fail_counts[key] = 0
        logger.info("[HYSTERESIS] counters reset")

    def _evaluate_gate_hysteresis(
        self,
        *,
        gate_name: str,
        failed: bool,
        snapshot: dict[str, Any],
    ) -> bool:
        threshold = max(1, int(self.gate_fail_thresholds.get(gate_name, 1)))
        prev = int(self.gate_fail_counts.get(gate_name, 0))

        if failed:
            count = prev + 1
            self.gate_fail_counts[gate_name] = count
            triggered = count >= threshold
            if not triggered:
                logger.warning(
                    "[HYSTERESIS] gate=%s fail %s/%s - holding",
                    gate_name,
                    count,
                    threshold,
                )
            else:
                logger.error(
                    "[HYSTERESIS] gate=%s failed %s/%s ticks - exit triggered",
                    gate_name,
                    count,
                    threshold,
                )
                self.gate_fail_counts[gate_name] = 0
        else:
            triggered = False
            if prev > 0:
                logger.info(
                    "[HYSTERESIS] gate=%s recovered after %s failing ticks - reset",
                    gate_name,
                    prev,
                )
            self.gate_fail_counts[gate_name] = 0

        hyst = snapshot.setdefault("hysteresis", {})
        hyst[gate_name] = {
            "threshold": threshold,
            "count": int(self.gate_fail_counts.get(gate_name, 0)),
            "failed": bool(failed),
            "triggered": bool(triggered),
        }
        return triggered

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

    def _evaluate_exit_logic(
        self,
        *,
        position_side: str,
        context: dict[str, Any],
        current_price: float,
        entry_price: float,
        bars_held: int,
        guard: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        is_long = position_side == "buy"
        composite_key = "composite_long" if is_long else "composite_short"
        pattern_key = "pattern_long" if is_long else "pattern_short"
        imbalance_key = "imbalance_long" if is_long else "imbalance_short"
        opposite_imbalance_key = "imbalance_short" if is_long else "imbalance_long"
        reversal_key = "reversal_long" if is_long else "reversal_short"

        unrealized_pct = (
            (current_price - entry_price) / entry_price
            if is_long
            else (entry_price - current_price) / entry_price
        )
        loss_pct = max(0.0, -unrealized_pct)
        confidence = float(context.get("confidence", 0.0))
        composite = float(context.get(composite_key, 0.0))
        trend = float(context.get("trend_score", 0.0))
        pattern = float(context.get(pattern_key, 0.0))
        imbalance = float(context.get(imbalance_key, 0.0))
        opposite_imbalance = float(context.get(opposite_imbalance_key, 0.0))

        snapshot: dict[str, Any] = {
            "position_side": position_side,
            "current_price": current_price,
            "entry_price": entry_price,
            "unrealized_pct": float(unrealized_pct),
            "loss_pct": float(loss_pct),
            "confidence": confidence,
            "composite": composite,
            "trend": trend,
            "pattern": pattern,
            "imbalance": imbalance,
            "opposite_imbalance": opposite_imbalance,
            "bars_held": int(bars_held),
            "stop_price_before": float(guard.get("stop_price", entry_price)),
            "thresholds": {
                "loss_cut": float(self.exit_max_loss_pct),
                "confidence_floor": float(self.exit_confidence_floor_pct),
                "composite_floor": float(self.exit_composite_floor),
                "trend_floor": float(self.exit_trend_floor),
                "pattern_floor": float(self.exit_pattern_floor),
                "imbalance_floor": float(self.exit_imbalance_floor),
                "opposite_imbalance_spike": float(self.exit_opposite_imbalance_spike),
            },
            "gates": {
                "loss_cut": loss_pct >= self.exit_max_loss_pct,
                "confidence_break": confidence < self.exit_confidence_floor_pct,
                "composite_break": composite < self.exit_composite_floor,
                "trend_break": trend < self.exit_trend_floor,
                "pattern_break": pattern < self.exit_pattern_floor,
                "imbalance_break": imbalance < self.exit_imbalance_floor,
                "opposite_imbalance_spike": opposite_imbalance > self.exit_opposite_imbalance_spike,
            },
        }
        snapshot["hysteresis_thresholds"] = dict(self.gate_fail_thresholds)

        reason = ""
        if self.enforce_exit_execution_gates:
            gate_checks = [
                ("loss_cut", loss_pct >= self.exit_max_loss_pct, self._get_loss_cut_label(is_long)),
                (
                    "confidence",
                    confidence < self.exit_confidence_floor_pct,
                    self._reason_with_side(ExitReason.CONFIDENCE_FLOOR, is_long),
                ),
                (
                    "composite",
                    composite < self.exit_composite_floor,
                    self._reason_with_side(ExitReason.COMPOSITE_FLOOR, is_long),
                ),
                (
                    "trend",
                    trend < self.exit_trend_floor,
                    self._reason_with_side(ExitReason.TREND_FLOOR, is_long),
                ),
                (
                    "pattern",
                    pattern < self.exit_pattern_floor,
                    self._reason_with_side(ExitReason.PATTERN_FLOOR, is_long),
                ),
                (
                    "imbalance",
                    imbalance < self.exit_imbalance_floor,
                    self._reason_with_side(ExitReason.IMBALANCE_FLOOR, is_long),
                ),
                (
                    "opposite_spike",
                    opposite_imbalance > self.exit_opposite_imbalance_spike,
                    self._reason_with_side(ExitReason.OPPOSITE_SPIKE, is_long),
                ),
            ]
            for gate_name, failed, gate_reason in gate_checks:
                if self._evaluate_gate_hysteresis(
                    gate_name=gate_name,
                    failed=bool(failed),
                    snapshot=snapshot,
                ):
                    reason = gate_reason
                    break

        r_value = float(guard.get("risk_r", self.exit_max_loss_pct))
        if unrealized_pct >= (1.0 * r_value):
            if is_long:
                guard["stop_price"] = max(float(guard.get("stop_price", entry_price)), entry_price)
            else:
                guard["stop_price"] = min(float(guard.get("stop_price", entry_price)), entry_price)
        if unrealized_pct >= (2.0 * r_value):
            trailing = (
                current_price * (1.0 - (0.75 * r_value))
                if is_long
                else current_price * (1.0 + (0.75 * r_value))
            )
            if is_long:
                guard["stop_price"] = max(float(guard.get("stop_price", entry_price)), trailing)
            else:
                guard["stop_price"] = min(float(guard.get("stop_price", entry_price)), trailing)

        stop_price = float(guard.get("stop_price", entry_price))
        if not reason:
            profit_lock_failed = (is_long and current_price <= stop_price) or ((not is_long) and current_price >= stop_price)
            if self._evaluate_gate_hysteresis(
                gate_name="profit_lock",
                failed=profit_lock_failed,
                snapshot=snapshot,
            ):
                reason = self._reason_with_side(ExitReason.PROFIT_LOCK, is_long)

        if not reason and self._evaluate_gate_hysteresis(
            gate_name="strong_reversal",
            failed=bool(context.get(reversal_key, False)),
            snapshot=snapshot,
        ):
            reason = self._reason_with_side(ExitReason.STRONG_REVERSAL, is_long)

        time_stop_failed = False
        if bars_held >= self.exit_time_stop_bars:
            entry_momentum = abs(float(guard.get("entry_momentum", 0.0)))
            current_momentum = abs(float(context.get("momentum", 0.0)))
            if current_momentum <= (entry_momentum * 1.05):
                time_stop_failed = True
        if not reason and self._evaluate_gate_hysteresis(
            gate_name="time_stop",
            failed=time_stop_failed,
            snapshot=snapshot,
        ):
            reason = self._reason_with_side(ExitReason.TIME_STOP, is_long)

        entry_atr = float(guard.get("entry_atr", 0.0))
        current_atr = float(context.get("atr", 0.0))
        volatility_contract_failed = (
            entry_atr > 0
            and current_atr > 0
            and current_atr <= (entry_atr * self.exit_volatility_contraction)
        )
        if not reason and self._evaluate_gate_hysteresis(
            gate_name="volatility_contract",
            failed=volatility_contract_failed,
            snapshot=snapshot,
        ):
            reason = self._reason_with_side(ExitReason.VOLATILITY_CONTRACT, is_long)

        vol_ma20 = float(context.get("vol_ma20", 0.0))
        vol_last = float(context.get("vol_last", 0.0))
        liquidity_vacuum_failed = vol_ma20 > 0 and vol_last < (vol_ma20 * self.exit_liquidity_vacuum_factor)
        if not reason and self._evaluate_gate_hysteresis(
            gate_name="liquidity_vacuum",
            failed=liquidity_vacuum_failed,
            snapshot=snapshot,
        ):
            reason = self._reason_with_side(ExitReason.LIQUIDITY_VACUUM, is_long)

        corr = abs(float(context.get("correlation", 0.0)))
        if not reason and self._evaluate_gate_hysteresis(
            gate_name="correlation_spike",
            failed=(corr >= self.exit_correlation_spike_abs),
            snapshot=snapshot,
        ):
            reason = self._reason_with_side(ExitReason.CORRELATION_SPIKE, is_long)

        snapshot["stop_price_after"] = float(guard.get("stop_price", entry_price))
        snapshot["reason"] = reason
        return reason, snapshot

    def _build_exit_signal(
        self,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str,
        exchange_symbol: str | None = None,
    ) -> dict[str, Any]:
        close_side = "sell" if str(position_side).lower() == "buy" else "buy"
        sig: dict[str, Any] = {
            "symbol": symbol,
            "side": close_side,
            "quantity": float(quantity),
            "order_type": "market",
            "order_kind": "taker",
            "strategy_id": "momentum_exit_v1",
            "regime": "risk_exit",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exit_reason": str(reason),
        }
        # Pass through exchange symbol so execution engine uses the correct market
        if exchange_symbol:
            sig["exchange_symbol"] = exchange_symbol
        return sig

    def _build_exit_signal_if_needed(self, candles: pd.DataFrame) -> dict[str, Any] | None:
        self.last_exit_gate_snapshot = {}
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

            # Update mark price for unrealized PnL during hold
            if current_price > 0:
                self.risk_manager.update_mark_price(symbol, current_price)

            guard = self._ensure_position_guard(symbol, side, entry_price)
            guard["highest_price"] = max(float(guard.get("highest_price", entry_price)), current_price)
            guard["lowest_price"] = min(float(guard.get("lowest_price", entry_price)), current_price)
            bars_held = max(0, len(self.candle_history) - int(guard.get("entry_candle_index", len(self.candle_history))))
            reason, snapshot = self._evaluate_exit_logic(
                position_side=side,
                context=context,
                current_price=current_price,
                entry_price=entry_price,
                bars_held=bars_held,
                guard=guard,
            )
            snapshot["symbol"] = symbol
            self.last_exit_gate_snapshot = snapshot

            if reason:
                self._record_exit_event(
                    symbol=symbol,
                    position_side=side,
                    reason=reason,
                    snapshot=snapshot,
                )
                # Use the actual exchange symbol to avoid inverse/linear mismatch
                actual_exchange_symbol = (
                    pos.get("exchange_symbol")
                    or self.exchange_position_cache.get(symbol, {}).get("symbol")
                )
                return self._build_exit_signal(
                    symbol=symbol,
                    position_side=side,
                    quantity=quantity,
                    reason=reason,
                    exchange_symbol=actual_exchange_symbol,
                )

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

    def _apply_auto_entry_sizing(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        if not self.entry_auto_size_enabled:
            return signal
        if getattr(self.execution_engine, "paper_mode", True):
            return signal

        side = str(signal.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            return signal

        try:
            incoming_qty = float(signal.get("quantity", 0.0) or 0.0)
        except (TypeError, ValueError):
            incoming_qty = 0.0
        if incoming_qty > 0 and not self.entry_auto_size_override_signal_qty:
            return signal

        reference_price = self._reference_price(signal)
        if reference_price is None or reference_price <= 0:
            signal["auto_size_blocked"] = True
            signal["auto_size_reason"] = "missing_reference_price"
            signal["quantity"] = 0.0
            return signal

        equity_candidates = [
            signal.get("equity"),
            signal.get("account_equity"),
            signal.get("account_balance"),
            getattr(self.risk_manager, "current_balance", None),
            getattr(self.risk_manager, "account_balance", None),
            self.account_balance,
        ]
        equity = 0.0
        for candidate in equity_candidates:
            try:
                value = float(candidate)
            except (TypeError, ValueError):
                continue
            if value > 0:
                equity = value
                break

        if equity <= 0:
            signal["auto_size_blocked"] = True
            signal["auto_size_reason"] = "missing_positive_equity"
            signal["quantity"] = 0.0
            return signal

        max_leverage = float(
            max(
                0.1,
                getattr(self.execution_engine, "max_leverage_ratio", getattr(self.risk_manager, "max_leverage_ratio", 5.0)),
            )
        )
        leverage_cap = max_leverage * self.entry_auto_size_leverage_cap_fraction
        target_leverage = min(self.entry_auto_size_target_leverage, leverage_cap)

        max_position_pct = float(max(0.0, getattr(self.risk_manager, "max_position_pct", 1.0)))
        risk_notional_cap = equity * max_position_pct
        leverage_notional_target = equity * target_leverage
        desired_notional = min(leverage_notional_target, risk_notional_cap * 0.995)
        max_contracts_limit = int(max(1, getattr(self.execution_engine, "max_contracts_hard_limit", 5)))

        symbol = str(signal.get("symbol", self.symbol))

        def _contract_bounds(sym: str) -> tuple[float, float, float, bool, float]:
            contract_size = float(self.execution_engine.get_contract_size(sym) or 1.0)
            inverse = bool(self.execution_engine._is_inverse_market(sym))
            min_notional = contract_size if inverse else (contract_size * float(reference_price))
            max_notional = (
                max_contracts_limit * contract_size
                if inverse
                else max_contracts_limit * contract_size * float(reference_price)
            )
            max_base_qty = (
                (max_contracts_limit * contract_size) / float(reference_price)
                if inverse
                else (max_contracts_limit * contract_size)
            )
            return min_notional, max_notional, max_base_qty, inverse, contract_size

        def _related_symbol_candidates(primary_symbol: str) -> list[str]:
            candidates: list[str] = [primary_symbol]

            upper = primary_symbol.upper()
            if upper.startswith("PF_"):
                candidates.append(primary_symbol.replace("PF_", "PI_", 1))
            elif upper.startswith("PI_"):
                candidates.append(primary_symbol.replace("PI_", "PF_", 1))

            exchange = getattr(self.execution_engine, "exchange", None)
            if exchange is not None:
                markets = getattr(exchange, "markets", None) or {}
                if markets:
                    resolved = None
                    try:
                        resolved = self.execution_engine._resolve_exchange_symbol(primary_symbol)
                    except Exception:
                        resolved = None

                    market = None
                    if resolved and resolved in markets:
                        market = markets.get(resolved)
                    elif primary_symbol in markets:
                        market = markets.get(primary_symbol)

                    if isinstance(market, dict):
                        base = str(market.get("base") or "")
                        quote = str(market.get("quote") or "")
                        if base and quote:
                            for m in markets.values():
                                if not isinstance(m, dict):
                                    continue
                                if not bool(m.get("contract")):
                                    continue
                                if str(m.get("base") or "") != base:
                                    continue
                                if str(m.get("quote") or "") != quote:
                                    continue
                                alt_symbol = str(m.get("symbol") or "").strip()
                                if alt_symbol:
                                    candidates.append(alt_symbol)

            dedup: list[str] = []
            seen: set[str] = set()
            for c in candidates:
                key = str(c or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                dedup.append(key)
            return dedup

        min_contract_notional, max_contract_notional, max_qty_by_contract, _inverse, _contract_size = _contract_bounds(symbol)
        chosen_symbol = symbol
        chosen_notional = min(desired_notional, max_contract_notional)
        chosen_max_qty_by_contract = max_qty_by_contract

        candidates = _related_symbol_candidates(symbol)
        best_symbol = chosen_symbol
        best_min_notional = min_contract_notional
        best_max_notional = max_contract_notional
        best_max_qty = chosen_max_qty_by_contract
        best_effective_notional = chosen_notional if chosen_notional >= min_contract_notional else -1.0

        for candidate in candidates[1:]:
            cand_min_notional, cand_max_notional, cand_max_qty, _cand_inverse, _cand_contract_size = _contract_bounds(candidate)
            cand_effective_notional = min(desired_notional, cand_max_notional)
            if cand_effective_notional < cand_min_notional:
                continue
            if cand_effective_notional > best_effective_notional:
                best_symbol = candidate
                best_min_notional = cand_min_notional
                best_max_notional = cand_max_notional
                best_max_qty = cand_max_qty
                best_effective_notional = cand_effective_notional

        chosen_symbol = best_symbol
        min_contract_notional = best_min_notional
        max_contract_notional = best_max_notional
        chosen_max_qty_by_contract = best_max_qty
        chosen_notional = best_effective_notional

        if chosen_notional < min_contract_notional:
            signal["auto_size_blocked"] = True
            signal["auto_size_reason"] = (
                f"min_contract_notional_exceeds_budget:{desired_notional:.2f}<{min_contract_notional:.2f}"
            )
            signal["quantity"] = 0.0
            signal["equity"] = float(equity)
            return signal

        if chosen_symbol != symbol:
            signal["symbol"] = chosen_symbol
            signal["auto_size_symbol_switched"] = True
            signal["auto_size_symbol_from"] = symbol
            signal["auto_size_symbol_to"] = chosen_symbol

        sized_qty = chosen_notional / float(reference_price)
        sized_qty = min(sized_qty, self.entry_auto_size_max_qty, chosen_max_qty_by_contract)
        if sized_qty <= 0:
            signal["auto_size_blocked"] = True
            signal["auto_size_reason"] = "non_positive_sized_quantity"
            signal["quantity"] = 0.0
            signal["equity"] = float(equity)
            return signal

        min_qty_floor = self.entry_auto_size_min_qty
        max_qty_feasible = min(self.entry_auto_size_max_qty, chosen_max_qty_by_contract)
        if min_qty_floor <= max_qty_feasible:
            sized_qty = max(min_qty_floor, sized_qty)

        signal["quantity"] = float(sized_qty)
        signal["equity"] = float(equity)
        signal["expected_price"] = signal.get("expected_price") or float(reference_price)
        signal["auto_sized"] = True
        signal["auto_size_notional"] = float(signal["quantity"] * float(reference_price))
        signal["auto_size_target_leverage"] = float(target_leverage)
        signal["auto_size_contract_notional_cap"] = float(max_contract_notional)
        signal["auto_size_contract_limit"] = int(max_contracts_limit)

        logger.info(
            "Auto-sized entry | symbol=%s side=%s equity=%.2f price=%.2f qty=%.6f notional=%.2f target_lev=%.2f",
            str(signal.get("symbol", symbol)),
            side,
            equity,
            reference_price,
            signal["quantity"],
            signal["auto_size_notional"],
            target_leverage,
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
                pnl = self.risk_manager.close_position(local_symbol, float(exit_price), fees=0.0)
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
                # Store the actual exchange symbol so exits resolve correctly
                self.risk_manager.positions[local_symbol]["exchange_symbol"] = exchange_symbol
                self._ensure_position_guard(
                    symbol=local_symbol,
                    side=str(row["side"]),
                    entry_price=float(row["entry_price"]),
                )
                logger.info(
                    "Synced exchange open | symbol=%s exchange=%s side=%s qty=%.6f entry=%.2f",
                    local_symbol,
                    exchange_symbol,
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
                # Preserve the actual exchange symbol
                local_pos["exchange_symbol"] = exchange_symbol
                # Update mark price for unrealized PnL tracking
                mark = float(row.get("mark_price", 0.0) or 0.0)
                if mark > 0:
                    self.risk_manager.update_mark_price(local_symbol, mark)
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
        """Start the momentum worker loop with watchdog."""
        self.is_running = True
        self._record_heartbeat()
        logger.info("Starting MomentumWorker for %s", self.symbol)

        while self.is_running:
            try:
                await self._run_iteration()
                self._record_heartbeat()
                await asyncio.sleep(self._interval_seconds())
            except asyncio.CancelledError:
                self.is_running = False
                logger.info("MomentumWorker task cancelled for %s", self.symbol)
                raise
            except Exception as e:
                logger.error("Worker iteration failed: %s", e)
                self._record_heartbeat()  # still alive, just errored
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
            await self._reconcile_partial_fills()

            status = self.risk_manager.get_status()
            drawdown = status.get("drawdown_pct", 0)
            if drawdown > 20.0:
                logger.critical("🛑 MAX DRAWDOWN BREACHED (%.2f%%) - STOPPING STRATEGY", drawdown)
                AlertManager.instance().send(
                    "critical", "Max Drawdown Breached",
                    f"Drawdown {drawdown:.2f}% exceeded limit — strategy stopped",
                    {"drawdown_pct": round(drawdown, 2), "symbol": self.symbol},
                )
                self.last_decision_reason = "stopped_max_drawdown"
                await self.stop()
                return

            ohlcv = await self._load_ohlcv(
                symbol=self.symbol,
                timeframe=self.interval,
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
                AlertManager.instance().send(
                    "warning", "Exit Signal",
                    f"Closing {exit_signal.get('symbol', self.symbol)} {exit_signal.get('side', '?')} — {exit_signal.get('exit_reason', 'unknown')}",
                    {"symbol": str(exit_signal.get('symbol', self.symbol)), "reason": str(exit_signal.get('exit_reason', 'unknown'))},
                )
                self.last_decision_reason = f"executing_exit:{str(exit_signal.get('exit_reason', 'unknown'))}"
                exit_result = self.execution_engine.execute(exit_signal)
                exit_status = str((exit_result or {}).get("status", "")).lower()
                if exit_status in {"rejected", "cancelled", "canceled", "blocked"}:
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
                    try:
                        AlertManager.instance().send(
                            "info", "Trade Closed",
                            f"Net PnL: ${float(trade_record.get('pnl', 0)):+.2f} | {str(exit_signal.get('exit_reason', 'unknown'))}",
                            {
                                "entry": f"${float(trade_record.get('entry_price', 0)):,.2f}",
                                "exit": f"${float(trade_record.get('exit_price', 0)):,.2f}",
                                "fees": f"${abs(float(trade_record.get('fees') or 0)):.4f}",
                                "symbol": str(exit_signal.get("symbol", self.symbol)),
                            },
                        )
                    except Exception:
                        pass
                    # Cancel native stop on exit
                    exit_sym = str(exit_signal.get("symbol", self.symbol))
                    await self._cancel_native_stop(exit_sym)
                if exit_result and exit_status not in {"rejected", "cancelled", "canceled", "blocked"}:
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
                    self.signal_history.append(
                        {
                            "symbol": str(signal.get("symbol", self.symbol)),
                            "side": side,
                            "quantity": float(signal.get("quantity", 0.0) or 0.0),
                            "status": "blocked",
                            "reason": gate_reason,
                            "block_reason": gate_reason,
                            "gate_snapshot": gate_snapshot,
                            "confidence_pct": float(gate_snapshot.get("confidence_pct", 0.0) or 0.0),
                            "timestamp": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    return

            signal = self._apply_live_order_preferences(signal)
            signal = self._apply_auto_entry_sizing(signal)

            # HTF multi-timeframe confirmation (async)
            if self.htf_enabled and side in {"buy", "sell"}:
                htf_ctx = await self._fetch_htf_context()
                htf_agrees, htf_trend = self._htf_trend_agrees(side, htf_ctx)
                signal.setdefault("gate_snapshot", {})
                if isinstance(signal.get("gate_snapshot"), dict):
                    signal["gate_snapshot"]["htf_trend"] = htf_trend
                    signal["gate_snapshot"]["htf_agrees"] = htf_agrees
                    signal["gate_snapshot"]["htf_timeframe"] = self.htf_timeframe
                if not htf_agrees:
                    logger.info(
                        "Skipping signal: HTF (%s) trend disagrees | side=%s htf_trend=%.4f",
                        self.htf_timeframe, side, htf_trend,
                    )
                    self.last_decision_reason = f"entry_gate_failed:htf_trend_{self.htf_timeframe}"
                    self.signal_history.append(
                        {
                            "symbol": str(signal.get("symbol", self.symbol)),
                            "side": side,
                            "quantity": float(signal.get("quantity", 0.0) or 0.0),
                            "status": "blocked",
                            "reason": self.last_decision_reason,
                            "block_reason": self.last_decision_reason,
                            "gate_snapshot": signal.get("gate_snapshot") if isinstance(signal.get("gate_snapshot"), dict) else {},
                            "confidence_pct": float(((signal.get("gate_snapshot") or {}).get("confidence_pct", 0.0)) if isinstance(signal.get("gate_snapshot"), dict) else 0.0),
                            "timestamp": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    return

            if bool(signal.get("auto_size_blocked")):
                auto_size_reason = str(signal.get("auto_size_reason") or "auto_size_blocked")
                logger.info("Skipping signal: %s | signal=%s", auto_size_reason, signal)
                self.last_decision_reason = f"entry_size_blocked:{auto_size_reason}"
                self.signal_history.append(
                    {
                        "symbol": str(signal.get("symbol", self.symbol)),
                        "side": side,
                        "quantity": float(signal.get("quantity", 0.0) or 0.0),
                        "status": "blocked",
                        "reason": self.last_decision_reason,
                        "block_reason": self.last_decision_reason,
                        "gate_snapshot": signal.get("gate_snapshot") if isinstance(signal.get("gate_snapshot"), dict) else {},
                        "confidence_pct": float(((signal.get("gate_snapshot") or {}).get("confidence_pct", 0.0)) if isinstance(signal.get("gate_snapshot"), dict) else 0.0),
                        "timestamp": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    }
                )
                return

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
                if result and result_status in {"rejected", "cancelled", "canceled", "blocked"}:
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

                    # Track for partial fill reconciliation
                    order_id = str((result or {}).get("id") or "")
                    if order_id:
                        self._track_pending_order(
                            order_id=order_id,
                            symbol=str(signal.get("symbol", self.symbol)),
                            side=side,
                            expected_qty=float(signal.get("quantity", 0)),
                        )

                    # Place native exchange stop loss
                    avg_fill = float((result or {}).get("avg_fill_price") or 0.0)
                    if avg_fill > 0 and not local_position_symbol:
                        await self._place_native_stop(
                            symbol=str(signal.get("symbol", self.symbol)),
                            side=side,
                            entry_price=avg_fill,
                            qty=float(signal.get("quantity", 0)),
                        )

                    if self.max_trades and self.trade_count >= self.max_trades:
                        logger.info("Reached max trades (%s). Stopping worker.", self.max_trades)
                        self.last_decision_reason = "stopped_max_trades"
                        await self.stop()
                        return

                    logger.info("Order placed: %s", result.get("id", result))
                    AlertManager.instance().send(
                        "info", "Entry Order Placed",
                        f"{side.upper()} {signal.get('symbol', self.symbol)} qty={signal.get('quantity')}",
                        {"order_id": str(result.get('id', '')), "side": side, "symbol": str(signal.get('symbol', self.symbol))},
                    )
                    self.last_decision_reason = "entry_submitted"
                    await self._sync_live_exchange_state()
                else:
                    order_record, trade_record = self._build_order_record(signal, None)
                    self.signal_history.append(order_record)
                    logger.warning("Execution returned no result")
                    self.last_decision_reason = "entry_execution_returned_none"
                    await self._sync_live_exchange_state()
            except Exception as exc:
                logger.exception("Order execution failed")
                AlertManager.instance().send(
                    "critical", "Execution Failed",
                    f"Order execution error: {exc}",
                    {"symbol": str(signal.get('symbol', self.symbol)), "error": str(exc)[:200]},
                )
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
            demo = self._sandbox_mode_from_env(True)
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
                    # Use position quantity (authoritative) not signal quantity
                    size = float(pos.get("quantity", 0.0) or signal.get("quantity", 0))
                    exit_price = avg_fill_price
                    
                    if pos_side == "buy":
                        gross_pnl = (exit_price - entry_price) * size
                    else:
                        gross_pnl = (entry_price - exit_price) * size

                    net_pnl = gross_pnl - abs(float(fees or 0.0))
                    pnl = net_pnl

                    outcome = "win" if net_pnl > 0 else ("loss" if net_pnl < 0 else "flat")

                    # Pass fees so risk manager deducts them from balance
                    self.risk_manager.close_position(local_symbol, exit_price, fees=abs(float(fees or 0.0)))
                    self.position_guards.pop(local_symbol, None)

                    trade_record = {
                        "timestamp": (result or {}).get("timestamp") or signal.get("timestamp"),
                        "symbol": local_symbol,
                        "side": "long" if pos_side == "buy" else "short",
                        "size": size,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "gross_pnl": gross_pnl,
                        "pnl": net_pnl,
                        "fees": fees,
                        "slippage": slippage,
                        "regime": regime,
                        "outcome": outcome,
                    }
            else:
                # Open new position
                self.risk_manager.open_position(local_symbol, side, signal.get("quantity", 0), avg_fill_price)
                self._ensure_position_guard(local_symbol, side, avg_fill_price)
                self._reset_hysteresis()
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
            "reason": (result or {}).get("reason") or (result or {}).get("error") or None,
            "block_reason": (result or {}).get("reason") if status == "blocked" else None,
            "confidence_pct": float(
                (
                    (signal.get("gate_snapshot") or {}).get("confidence_pct")
                    if isinstance(signal.get("gate_snapshot"), dict)
                    else signal.get("confidence_pct", signal.get("confidence", 0.0))
                )
                or 0.0
            ),
            "gate_snapshot": signal.get("gate_snapshot") if isinstance(signal.get("gate_snapshot"), dict) else {},
        }

        return order_record, trade_record

    def get_analytics(self) -> dict[str, Any]:
        trades = [t for t in self.trade_history if t.get("pnl") is not None]
        # net PnL (after fees) — this is the authoritative metric
        pnls = [float(t["pnl"]) for t in trades]
        # gross PnL for reference
        gross_pnls = [float(t.get("gross_pnl", t["pnl"])) for t in trades]
        total_fees = sum(abs(float(t.get("fees") or 0.0)) for t in trades)
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
        session_gross_pnl = sum(gross_pnls) if gross_pnls else 0.0

        # Unrealized PnL from open positions
        unrealized_pnl = self.risk_manager.get_total_unrealized_pnl()

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
            "session_gross_pnl": round(session_gross_pnl, 4),
            "total_fees": round(total_fees, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "equity": round(self.risk_manager.get_equity(), 2),
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
            "last_exit_gate_snapshot": self.last_exit_gate_snapshot,
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

            # Use actual exchange symbol to avoid inverse/linear mismatch
            actual_exchange_symbol = (
                pos.get("exchange_symbol")
                or self.exchange_position_cache.get(symbol, {}).get("symbol")
            )

            signal: dict[str, Any] = {
                "symbol": symbol,
                "side": close_side,
                "quantity": qty,
                "order_type": "market",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "strategy_id": "manual_close",
            }
            if actual_exchange_symbol:
                signal["exchange_symbol"] = actual_exchange_symbol

            result = self.execution_engine.execute(signal)
            order_record, trade_record = self._build_order_record(signal, result)
            self.signal_history.append(order_record)

            if trade_record:
                self.trade_history.append(trade_record)
                self._persist_trade(trade_record)

                AlertManager.instance().send(
                "warning", "Force Close",
                f"Manually closed {symbol} {close_side}",
                {"symbol": symbol, "side": close_side, "qty": qty},
            )
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
