import asyncio
import logging

import pytest

from app.services.startup_safety import startup_safety_check


class _DummyEngine:
    def __init__(self, positions, exit_result=None) -> None:
        self.paper_mode = False
        self.max_contracts_hard_limit = 5
        self.max_leverage_ratio = 5.0
        self._positions = list(positions)
        self._exit_result = exit_result or {
            "status": "exit_attempted",
            "chunks": [{"chunk": 1, "result": {"status": "filled"}}],
        }
        self.exit_calls = []

    async def get_open_positions_async(self):
        return list(self._positions)

    async def get_mark_price_async(self, symbol: str):
        return 81700.0

    def get_contract_size(self, symbol: str):
        return 1.0

    async def emergency_exit_position_async(self, **kwargs):
        self.exit_calls.append(dict(kwargs))
        return dict(self._exit_result)


class _DummyRiskManager:
    def __init__(self, equity: float = 5497.0) -> None:
        self.current_balance = float(equity)
        self.max_leverage_ratio = 5.0


def test_startup_safety_clean_start_no_positions() -> None:
    engine = _DummyEngine([])
    risk = _DummyRiskManager()
    asyncio.run(
        startup_safety_check(
            execution_engine=engine,
            risk_manager=risk,
            momentum_worker=object(),
            logger=logging.getLogger("test_startup"),
        )
    )
    assert engine.exit_calls == []


def test_startup_safety_oversized_position_triggers_exit() -> None:
    engine = _DummyEngine(
        [
            {
                "symbol": "BTC/USD:USD",
                "contracts": 3.0,
                "side": "buy",
                "mark_price": 81700.0,
                "contract_size": 1.0,
                "inverse": False,
            }
        ]
    )
    risk = _DummyRiskManager(5497.0)

    asyncio.run(
        startup_safety_check(
            execution_engine=engine,
            risk_manager=risk,
            momentum_worker=object(),
            logger=logging.getLogger("test_startup"),
        )
    )

    assert len(engine.exit_calls) == 1
    kwargs = engine.exit_calls[0]
    assert kwargs.get("is_exit") is True
    assert kwargs.get("reason") == "startup_leverage_violation"


def test_startup_safety_failed_exit_aborts_startup() -> None:
    engine = _DummyEngine(
        [
            {
                "symbol": "BTC/USD:USD",
                "contracts": 3.0,
                "side": "buy",
                "mark_price": 81700.0,
                "contract_size": 1.0,
                "inverse": False,
            }
        ],
        exit_result={
            "status": "exit_attempted",
            "chunks": [{"chunk": 1, "error": "exchange timeout"}],
        },
    )
    risk = _DummyRiskManager(5497.0)

    with pytest.raises(RuntimeError, match="Emergency exit FAILED"):
        asyncio.run(
            startup_safety_check(
                execution_engine=engine,
                risk_manager=risk,
                momentum_worker=object(),
                logger=logging.getLogger("test_startup"),
            )
        )


def test_startup_safety_safe_position_no_exit() -> None:
    engine = _DummyEngine(
        [
            {
                "symbol": "BTC/USD:USD",
                "contracts": 0.1,
                "side": "buy",
                "mark_price": 72000.0,
                "contract_size": 1.0,
                "inverse": False,
            }
        ]
    )
    risk = _DummyRiskManager(5497.0)
    asyncio.run(
        startup_safety_check(
            execution_engine=engine,
            risk_manager=risk,
            momentum_worker=object(),
            logger=logging.getLogger("test_startup"),
        )
    )
    assert engine.exit_calls == []
