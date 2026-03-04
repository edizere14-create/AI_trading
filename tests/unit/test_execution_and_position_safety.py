import pytest

from engine.core.execution_engine import ExecutionEngine
from engine.workers.momentum_worker import MomentumWorker


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


def test_risk_exit_orders_are_reduce_only_for_futures() -> None:
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


def test_reduce_only_would_not_reduce_is_treated_as_cancelled() -> None:
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


def test_insufficient_funds_returns_rejected_payload() -> None:
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
