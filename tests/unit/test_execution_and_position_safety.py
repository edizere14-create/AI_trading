import pytest
import pandas as pd

from engine.core.execution_engine import ExecutionEngine
from engine.core.risk_manager import RiskManager
from engine.workers.momentum_worker import ExitReason, MomentumWorker


class _DummyExchange:
    def __init__(self) -> None:
        market = {
            "symbol": "BTC/USD:BTC",
            "contract": True,
            "contractSize": 1.0,
            "inverse": True,
            "linear": False,
        }
        self.markets = {"BTC/USD:BTC": market}
        self.markets_by_id = {"PI_XBTUSD": market}
        self._last_amount = 0.0
        self.last_params = {}

    def market(self, symbol: str):
        return self.markets[symbol]

    def price_to_precision(self, symbol: str, price: float) -> str:
        return str(price)

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return str(amount)

    def fetch_ticker(self, symbol: str):
        return {"last": 50000.0}

    def create_order(self, symbol: str, type: str, side: str, amount: float, price=None, params=None):
        self._last_amount = float(amount)
        self.last_params = dict(params or {})
        return {"id": "order-1", "status": "closed", "filled": float(amount), "cost": float(amount) * 50000.0}

    def fetch_order(self, order_id: str, symbol: str):
        return {"id": order_id, "status": "closed", "filled": self._last_amount, "cost": self._last_amount * 50000.0}

    def cancel_order(self, order_id: str, symbol: str):
        return {"id": order_id}


class _WouldNotReduceExchange(_DummyExchange):
    def create_order(self, symbol: str, type: str, side: str, amount: float, price=None, params=None):
        self._last_amount = float(amount)
        self.last_params = dict(params or {})
        raise Exception("krakenfutures: createOrder failed due to wouldNotReducePosition")


class _InsufficientFundsExchange(_DummyExchange):
    def create_order(self, symbol: str, type: str, side: str, amount: float, price=None, params=None):
        self._last_amount = float(amount)
        self.last_params = dict(params or {})
        raise Exception("krakenfutures: createOrder failed due to insufficientAvailableFunds")


class _DualMarketExchange(_DummyExchange):
    def __init__(self) -> None:
        super().__init__()
        linear_market = {
            "symbol": "BTC/USD:USD",
            "contract": True,
            "contractSize": 1.0,
            "inverse": False,
            "linear": True,
        }
        self.markets["BTC/USD:USD"] = linear_market


class _BlockingRiskManager:
    def pre_trade_notional_check(
        self,
        contracts: int,
        contract_size: float,
        mark_price: float,
        symbol: str,
        inverse: bool = False,
    ) -> bool:
        return False


class _MinimalExecutionEngine:
    def __init__(self, paper_mode: bool) -> None:
        self.paper_mode = paper_mode


def test_risk_exit_orders_are_reduce_only_for_futures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5000")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "100.0")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "10000")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _DummyExchange()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "sell",
            "quantity": 0.001,
            "order_kind": "taker",
            "strategy_id": "momentum_exit_v1",
            "regime": "risk_exit",
        }
    )

    assert result is not None
    assert engine.exchange.last_params.get("reduceOnly") is True
    assert engine.exchange._last_amount == pytest.approx(50.0)


def test_reduce_only_bypasses_hard_size_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "1")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "1.0")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "100")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _DummyExchange()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "sell",
            "quantity": 0.001,
            "order_kind": "taker",
            "strategy_id": "momentum_exit_v1",
            "regime": "risk_exit",
        }
    )

    assert result is not None
    assert result["status"] == "filled"
    assert engine.exchange.last_params.get("reduceOnly") is True
    assert engine.exchange._last_amount == pytest.approx(50.0)


def test_reduce_only_would_not_reduce_is_treated_as_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5000")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "100.0")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "10000")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _WouldNotReduceExchange()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "buy",
            "quantity": 0.001,
            "order_kind": "taker",
            "strategy_id": "momentum_exit_v1",
            "regime": "risk_exit",
        }
    )

    assert result is not None
    assert result["status"] == "cancelled"
    assert (result.get("raw") or {}).get("reason") == "would_not_reduce_position"


def test_insufficient_funds_returns_rejected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5000")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "100.0")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "10000")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _InsufficientFundsExchange()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "buy",
            "quantity": 0.001,
            "order_kind": "taker",
            "strategy_id": "momentum_v1",
        }
    )

    assert result is not None
    assert result["status"] == "rejected"
    assert result.get("reason") == "insufficient_funds"


def test_contract_inflation_guard_blocks_oversized_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "100")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "10000")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _DummyExchange()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "buy",
            "quantity": 0.001,
            "order_kind": "taker",
            "strategy_id": "momentum_v1",
        }
    )

    assert result is not None
    assert result["status"] == "blocked"
    assert result["reason"] == "size_guard_blocked"
    assert "hard limit" in str(result.get("error", "")).lower()
    assert engine.exchange._last_amount == pytest.approx(0.0)


def test_leverage_guard_blocks_excessive_notional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5000")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "1.0")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "100")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _DummyExchange()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "buy",
            "quantity": 0.01,
            "order_kind": "taker",
            "strategy_id": "momentum_v1",
        }
    )

    assert result is not None
    assert result["status"] == "blocked"
    assert result["reason"] == "size_guard_blocked"
    assert "leverage guard triggered" in str(result.get("error", "")).lower()
    assert engine.exchange._last_amount == pytest.approx(0.0)


def test_convert_and_validate_contracts_skips_guards_for_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "1")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "1.0")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    contracts, raw, notional, leverage = engine._convert_and_validate_contracts(
        base_qty=2.6614,
        contract_size=1.0,
        equity=5497.0,
        mark_price=81700.0,
        symbol="BTC/USD:USD",
        inverse=False,
        is_exit=True,
        reduce_only=True,
    )

    assert contracts >= 1
    assert raw > 0
    assert notional > 0
    assert leverage > 1.0


def test_convert_and_validate_contracts_blocks_entry_on_high_leverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5000")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "1.0")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    with pytest.raises(ValueError, match="Leverage guard triggered"):
        engine._convert_and_validate_contracts(
            base_qty=2.6614,
            contract_size=1.0,
            equity=5497.0,
            mark_price=81700.0,
            symbol="BTC/USD:USD",
            inverse=False,
            is_exit=False,
            reduce_only=False,
        )


def test_execution_honors_risk_manager_pre_trade_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONTRACTS_HARD_LIMIT", "5000")
    monkeypatch.setenv("MAX_LEVERAGE_RATIO", "50.0")
    monkeypatch.setenv("MOMENTUM_ACCOUNT_BALANCE", "10000")

    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _DummyExchange()
    engine.risk_manager = _BlockingRiskManager()
    engine.max_retries = 1

    result = engine.execute(
        {
            "symbol": "PI_XBTUSD",
            "side": "buy",
            "quantity": 0.001,
            "order_kind": "taker",
            "strategy_id": "momentum_v1",
        }
    )

    assert result is not None
    assert result["status"] == "blocked"
    assert result["reason"] == "risk_manager_leverage_block"
    assert engine.exchange._last_amount == pytest.approx(0.0)


def test_risk_manager_pre_trade_notional_check_enforces_max_leverage() -> None:
    rm = RiskManager(
        initial_balance=1000.0,
        max_position_pct=0.5,
        max_daily_loss_pct=0.1,
        max_drawdown_pct=0.2,
        max_concurrent_positions=3,
        max_leverage_ratio=2.0,
    )

    assert rm.pre_trade_notional_check(
        contracts=1500,
        contract_size=1.0,
        mark_price=50000.0,
        symbol="PI_XBTUSD",
        inverse=True,
    ) is True
    assert rm.pre_trade_notional_check(
        contracts=2500,
        contract_size=1.0,
        mark_price=50000.0,
        symbol="PI_XBTUSD",
        inverse=True,
    ) is False


def test_symbol_resolution_prefers_linear_alias_when_available() -> None:
    engine = ExecutionEngine(
        exchange_id="krakenfutures",
        api_key="x",
        api_secret="y",
        paper_mode=True,
        sandbox=True,
    )
    engine.paper_mode = False
    engine.exchange = _DualMarketExchange()

    resolved = engine._resolve_exchange_symbol("PI_XBTUSD")
    assert resolved == "BTC/USD:USD"


def test_symbol_matching_is_strict_after_normalization() -> None:
    assert MomentumWorker._symbols_match("PI_XBTUSD", "BTC/USD:BTC")
    assert not MomentumWorker._symbols_match("PI_XBTUSD", "FI_XBTUSD_260327")


def test_contracts_to_base_quantity_uses_inverse_hint() -> None:
    qty = MomentumWorker._contracts_to_base_quantity(
        symbol="PI_XBTUSD",
        contracts=67.638,
        price=67638.0,
        contract_size=1.0,
        is_inverse=True,
    )
    assert qty == pytest.approx(0.001, rel=1e-4)


def test_high_confidence_forces_market_taker_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_LIVE_MAKER_ONLY", "true")
    monkeypatch.setenv("MOMENTUM_LIVE_TAKER_ON_HIGH_CONFIDENCE", "true")
    monkeypatch.setenv("MOMENTUM_LIVE_TAKER_CONFIDENCE_THRESHOLD", "80")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    worker._last_context_metrics = {"confidence": 95.0}

    signal = {"side": "buy", "price": 73000.0, "order_type": "limit", "order_kind": "maker"}
    updated = worker._apply_live_order_preferences(signal)

    assert updated["order_type"] == "market"
    assert updated["order_kind"] == "taker"
    assert "price" not in updated
    assert updated.get("expected_price") == pytest.approx(73000.0)


def test_lower_confidence_keeps_maker_limit_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_LIVE_MAKER_ONLY", "true")
    monkeypatch.setenv("MOMENTUM_LIVE_MAKER_OFFSET_BPS", "8")
    monkeypatch.setenv("MOMENTUM_LIVE_TAKER_ON_HIGH_CONFIDENCE", "true")
    monkeypatch.setenv("MOMENTUM_LIVE_TAKER_CONFIDENCE_THRESHOLD", "90")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    worker._last_context_metrics = {"confidence": 65.0}

    signal = {"side": "sell", "price": 73000.0}
    updated = worker._apply_live_order_preferences(signal)

    assert updated["order_type"] == "limit"
    assert updated["order_kind"] == "maker"
    assert updated["price"] == pytest.approx(73000.0 * (1.0 + 0.0008), rel=1e-6)


def _build_trend_candles(rows: int = 60, base: float = 70000.0, step: float = 25.0) -> pd.DataFrame:
    close = [base + (i * step) for i in range(rows)]
    open_ = [c - 15.0 for c in close]
    high = [c + 10.0 for c in close]
    low = [c - 30.0 for c in close]
    volume = [1000.0 + (i % 5) for i in range(rows)]
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_demo_entry_gate_defaults_apply_in_sandbox_when_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAKEN_SANDBOX", "true")
    monkeypatch.delenv("MOMENTUM_DEMO_RELAXED_ENTRY_GATES", raising=False)
    monkeypatch.delenv("MOMENTUM_ENTRY_CONF_GATE_PCT", raising=False)
    monkeypatch.delenv("MOMENTUM_CONFIDENCE_GATE", raising=False)
    monkeypatch.delenv("MOMENTUM_ENTRY_CONVICTION_GATE", raising=False)
    monkeypatch.delenv("MOMENTUM_ENTRY_AGREEMENT_GATE", raising=False)

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )

    assert worker.entry_confidence_gate_pct == pytest.approx(20.0)
    assert worker.entry_conviction_gate == pytest.approx(0.12)
    assert worker.entry_agreement_gate == pytest.approx(0.20)


def test_live_entry_gate_defaults_stay_strict_when_sandbox_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAKEN_SANDBOX", "false")
    monkeypatch.delenv("MOMENTUM_DEMO_RELAXED_ENTRY_GATES", raising=False)
    monkeypatch.delenv("MOMENTUM_ENTRY_CONF_GATE_PCT", raising=False)
    monkeypatch.delenv("MOMENTUM_CONFIDENCE_GATE", raising=False)
    monkeypatch.delenv("MOMENTUM_ENTRY_CONVICTION_GATE", raising=False)
    monkeypatch.delenv("MOMENTUM_ENTRY_AGREEMENT_GATE", raising=False)

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )

    assert worker.entry_confidence_gate_pct == pytest.approx(55.0)
    assert worker.entry_conviction_gate == pytest.approx(0.35)
    assert worker.entry_agreement_gate == pytest.approx(0.30)


def test_entry_gate_blocks_low_confidence_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENFORCE_EXECUTION_GATES", "true")
    monkeypatch.setenv("MOMENTUM_ENTRY_CONF_GATE_PCT", "55")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    candles = _build_trend_candles()
    worker._last_context_metrics = {
        "confidence": 20.0,
        "pattern_long": 0.5,
        "pattern_short": 0.1,
    }
    worker.signal_history.append({"side": "buy"})
    worker.signal_history.append({"side": "buy"})
    worker.signal_history.append({"side": "sell"})

    allowed, reason, snapshot = worker._entry_gate_allows_execution(candles, "buy")

    assert allowed is False
    assert reason == "entry_gate_failed:confidence"
    assert snapshot["confidence_gate"] is False


def test_entry_gate_allows_high_quality_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENFORCE_EXECUTION_GATES", "true")
    monkeypatch.setenv("MOMENTUM_ENTRY_CONF_GATE_PCT", "55")
    monkeypatch.setenv("MOMENTUM_ENTRY_CONVICTION_GATE", "0.35")
    monkeypatch.setenv("MOMENTUM_ENTRY_AGREEMENT_GATE", "0.30")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    candles = _build_trend_candles()
    worker._last_context_metrics = {
        "confidence": 95.0,
        "pattern_long": 0.55,
        "pattern_short": 0.05,
    }
    worker.signal_history.append({"side": "buy"})
    worker.signal_history.append({"side": "buy"})
    worker.signal_history.append({"side": "buy"})
    worker.signal_history.append({"side": "sell"})

    allowed, reason, snapshot = worker._entry_gate_allows_execution(candles, "buy")

    assert allowed is True
    assert reason == "entry_gate_pass"
    assert snapshot["confidence_gate"] is True
    assert snapshot["direction_gate"] is True


def test_exit_gate_triggers_on_low_confidence_long(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("MOMENTUM_ENFORCE_EXIT_GATES", "true")
    monkeypatch.setenv("MOMENTUM_EXIT_CONFIDENCE_FLOOR_PCT", "50")
    monkeypatch.setenv("HYSTERESIS_CONFIDENCE", "1")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    worker.risk_manager.open_position("PI_XBTUSD", "buy", 0.001, 73000.0)

    context = {
        "price": 73100.0,
        "confidence": 30.0,
        "composite_long": 0.40,
        "trend_score": 0.60,
        "pattern_long": 0.50,
        "imbalance_long": 0.55,
        "imbalance_short": 0.10,
        "momentum": 1.0,
        "atr": 45.0,
        "vol_ma20": 1000.0,
        "vol_last": 1000.0,
        "correlation": 0.1,
        "reversal_long": False,
    }
    worker._compute_context_metrics = lambda _: context  # type: ignore[assignment]

    exit_signal = worker._build_exit_signal_if_needed(_build_trend_candles(60))

    assert exit_signal is not None
    assert exit_signal["side"] == "sell"
    assert exit_signal["exit_reason"] == ExitReason.CONFIDENCE_FLOOR.value
    assert worker.last_exit_gate_snapshot.get("reason") == ExitReason.CONFIDENCE_FLOOR.value


def test_exit_gate_does_not_trigger_when_conditions_are_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENFORCE_EXIT_GATES", "true")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    worker.risk_manager.open_position("PI_XBTUSD", "buy", 0.001, 73000.0)

    context = {
        "price": 73500.0,
        "confidence": 92.0,
        "composite_long": 0.50,
        "trend_score": 0.60,
        "pattern_long": 0.55,
        "imbalance_long": 0.60,
        "imbalance_short": 0.10,
        "momentum": 1.8,
        "atr": 45.0,
        "vol_ma20": 1000.0,
        "vol_last": 1000.0,
        "correlation": 0.2,
        "reversal_long": False,
    }
    worker._compute_context_metrics = lambda _: context  # type: ignore[assignment]

    exit_signal = worker._build_exit_signal_if_needed(_build_trend_candles(60))

    assert exit_signal is None
    assert worker.last_exit_gate_snapshot.get("reason", "") == ""


def test_exit_gate_equality_boundaries_do_not_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENFORCE_EXIT_GATES", "true")
    monkeypatch.setenv("HYSTERESIS_CONFIDENCE", "1")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )

    guard = {
        "stop_price": 72000.0,
        "risk_r": 0.02,
        "entry_momentum": 1.0,
        "entry_atr": 45.0,
    }
    context = {
        "confidence": 50.0,
        "composite_long": 0.15,
        "trend_score": 0.10,
        "pattern_long": 0.10,
        "imbalance_long": 0.10,
        "imbalance_short": 0.25,
        "momentum": 1.1,
        "atr": 50.0,
        "vol_ma20": 1000.0,
        "vol_last": 1000.0,
        "correlation": 0.10,
        "reversal_long": False,
    }

    reason, snapshot = worker._evaluate_exit_logic(
        position_side="buy",
        context=context,
        current_price=73100.0,
        entry_price=73000.0,
        bars_held=0,
        guard=guard,
    )

    assert reason == ""
    assert snapshot["gates"]["confidence_break"] is False
    assert snapshot["gates"]["composite_break"] is False
    assert snapshot["gates"]["trend_break"] is False
    assert snapshot["gates"]["pattern_break"] is False
    assert snapshot["gates"]["imbalance_break"] is False
    assert snapshot["gates"]["opposite_imbalance_spike"] is False


def test_loss_cut_label_reflects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_EXIT_MAX_LOSS_PCT", "0.035")
    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    assert worker._get_loss_cut_label(is_long=True) == f"{ExitReason.LOSS_CUT.value}_3.5pct"
    assert worker._get_loss_cut_label(is_long=False) == f"{ExitReason.LOSS_CUT.value}_3.5pct_short"


def test_confidence_exit_hysteresis_requires_two_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENFORCE_EXIT_GATES", "true")
    monkeypatch.setenv("MOMENTUM_EXIT_CONFIDENCE_FLOOR_PCT", "50")
    monkeypatch.setenv("HYSTERESIS_CONFIDENCE", "2")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    worker.risk_manager.open_position("PI_XBTUSD", "buy", 0.001, 73000.0)

    weak_context = {
        "price": 73100.0,
        "confidence": 30.0,
        "composite_long": 0.40,
        "trend_score": 0.60,
        "pattern_long": 0.50,
        "imbalance_long": 0.55,
        "imbalance_short": 0.10,
        "momentum": 1.0,
        "atr": 45.0,
        "vol_ma20": 1000.0,
        "vol_last": 1000.0,
        "correlation": 0.1,
        "reversal_long": False,
    }
    worker._compute_context_metrics = lambda _: weak_context  # type: ignore[assignment]

    first = worker._build_exit_signal_if_needed(_build_trend_candles(60))
    assert first is None
    assert worker.gate_fail_counts["confidence"] == 1

    second = worker._build_exit_signal_if_needed(_build_trend_candles(60))
    assert second is not None
    assert second["exit_reason"] == ExitReason.CONFIDENCE_FLOOR.value
    assert worker.gate_fail_counts["confidence"] == 0


def test_hysteresis_recovery_resets_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENFORCE_EXIT_GATES", "true")
    monkeypatch.setenv("HYSTERESIS_CONFIDENCE", "3")

    worker = MomentumWorker(
        symbol="PI_XBTUSD",
        execution_engine=_MinimalExecutionEngine(paper_mode=False),  # type: ignore[arg-type]
        data_service=object(),  # type: ignore[arg-type]
    )
    guard = {
        "stop_price": 72000.0,
        "risk_r": 0.02,
        "entry_momentum": 1.0,
        "entry_atr": 45.0,
    }
    weak_context = {
        "confidence": 30.0,
        "composite_long": 0.40,
        "trend_score": 0.60,
        "pattern_long": 0.50,
        "imbalance_long": 0.55,
        "imbalance_short": 0.10,
        "momentum": 1.0,
        "atr": 45.0,
        "vol_ma20": 1000.0,
        "vol_last": 1000.0,
        "correlation": 0.1,
        "reversal_long": False,
    }
    healthy_context = dict(weak_context)
    healthy_context["confidence"] = 95.0

    reason, _ = worker._evaluate_exit_logic(
        position_side="buy",
        context=weak_context,
        current_price=73100.0,
        entry_price=73000.0,
        bars_held=0,
        guard=guard,
    )
    assert reason == ""
    assert worker.gate_fail_counts["confidence"] == 1

    reason, _ = worker._evaluate_exit_logic(
        position_side="buy",
        context=healthy_context,
        current_price=73100.0,
        entry_price=73000.0,
        bars_held=0,
        guard=guard,
    )
    assert reason == ""
    assert worker.gate_fail_counts["confidence"] == 0
