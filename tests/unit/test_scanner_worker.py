from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pandas as pd

from engine.workers import scanner_worker as sw


class _FakeMomentumWorker:
    def __init__(self, positions: dict[str, dict[str, Any]] | None = None) -> None:
        self.risk_manager = SimpleNamespace(positions=positions or {})
        self.load_calls: list[str] = []

    async def _load_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        self.load_calls.append(symbol)
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-03-05T00:00:00Z", periods=3, freq="min"),
                "open": [1.0, 1.1, 1.2],
                "high": [1.1, 1.2, 1.3],
                "low": [0.9, 1.0, 1.1],
                "close": [1.0, 1.1, 1.2],
                "volume": [10.0, 11.0, 12.0],
            }
        )

    def _entry_gate_allows_execution(self, candles: pd.DataFrame, side: str):
        score = 60.0 if side == "buy" else 40.0
        return True, "ok", {"confidence_pct": score}


def test_correlation_guard_blocks_same_group_symbol() -> None:
    worker = _FakeMomentumWorker(positions={"PF_XBTUSD": {"side": "buy"}})
    scanner = sw.ScannerWorker(worker)

    allowed_eth = scanner._correlation_guard("PF_ETHUSD", scanner._open_positions_snapshot())
    allowed_doge = scanner._correlation_guard("PF_DOGEUSD", scanner._open_positions_snapshot())

    assert allowed_eth is False
    assert allowed_doge is True


def test_scan_skips_correlated_symbols_and_keeps_uncorrelated(monkeypatch) -> None:
    worker = _FakeMomentumWorker(positions={"PF_XBTUSD": {"side": "buy"}})
    scanner = sw.ScannerWorker(worker)

    monkeypatch.setattr(sw, "SCAN_SYMBOLS", ["PF_ETHUSD", "PF_DOGEUSD"])

    result = asyncio.run(scanner.scan())

    assert result is not None
    assert result["symbol"] == "PF_DOGEUSD"
    assert worker.load_calls == ["PF_DOGEUSD"]
