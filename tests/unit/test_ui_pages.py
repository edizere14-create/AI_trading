from __future__ import annotations

from dataclasses import dataclass
import importlib
import sys
from types import SimpleNamespace
import types
from typing import Any

import pandas as pd


@dataclass
class _FakeBlock:
    parent: "_FakeStreamlit"

    def __enter__(self) -> "_FakeBlock":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def metric(self, label: str, value: Any) -> None:
        self.parent.metrics.append((str(label), str(value)))

    def slider(
        self,
        label: str,
        min_value: float = 0.0,
        max_value: float = 100.0,
        value: float = 0.0,
        step: float = 1.0,
    ) -> float:
        return float(value)


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, str]] = []
        self.captions: list[str] = []
        self.warnings: list[str] = []
        self.session_state: dict[str, Any] = {}

    def columns(self, spec: Any):
        count = int(spec) if isinstance(spec, int) else len(spec)
        return tuple(_FakeBlock(self) for _ in range(count))

    def expander(self, label: str):
        return _FakeBlock(self)

    def subheader(self, text: str) -> None:
        return None

    def caption(self, text: str) -> None:
        self.captions.append(str(text))

    def write(self, text: Any) -> None:
        return None

    def markdown(self, text: str) -> None:
        return None

    def success(self, text: str) -> None:
        return None

    def error(self, text: str) -> None:
        return None

    def warning(self, text: str) -> None:
        self.warnings.append(str(text))

    def info(self, text: str) -> None:
        return None

    def dataframe(self, data: Any, **kwargs: Any) -> None:
        return None


def _load_pages_module():
    asyncpg_stub = sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
    if not hasattr(asyncpg_stub, "connect"):
        async def _connect(*args: Any, **kwargs: Any):
            raise RuntimeError("asyncpg test stub: connect should not be called")
        asyncpg_stub.connect = _connect  # type: ignore[attr-defined]

    ccxt_stub = sys.modules.setdefault("ccxt", types.ModuleType("ccxt"))
    if not hasattr(ccxt_stub, "Exchange"):
        ccxt_stub.Exchange = object  # type: ignore[attr-defined]

    return importlib.import_module("app.ui.pages")


def _seed_dashboard_mocks(monkeypatch, pages, worker_status: dict[str, Any]) -> _FakeStreamlit:
    fake_st = _FakeStreamlit()
    monkeypatch.setattr(pages, "st", fake_st)

    monkeypatch.setattr(
        pages,
        "get_metrics",
        lambda api_url: {
            "total_equity": 5000.0,
            "daily_pnl": 0.0,
            "ai_bias": "SELL",
            "confidence": 99.0,
            "risk_exposure": 0.0,
        },
    )
    monkeypatch.setattr(
        pages,
        "get_ai_insight",
        lambda api_url: {
            "bias": "SELL",
            "confidence": 99.0,
            "vol_forecast": 0.20,
            "pattern_summary": "trend=-0.1%, momentum10=-1.1%",
            "why": "test",
            "signals": [],
        },
    )
    monkeypatch.setattr(pages, "get_active_trades", lambda api_url: pd.DataFrame())
    monkeypatch.setattr(pages, "get_open_orders", lambda api_url: pd.DataFrame())

    candles = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-03-05T06:00:00Z", periods=4, freq="min"),
            "open": [72000.0, 72020.0, 72010.0, 72030.0],
            "high": [72030.0, 72040.0, 72035.0, 72050.0],
            "low": [71990.0, 72000.0, 71995.0, 72010.0],
            "close": [72010.0, 72015.0, 72012.0, 72018.0],
            "volume": [10.0, 9.0, 11.0, 8.0],
        }
    )
    monkeypatch.setattr(pages, "get_candles", lambda api_url, limit=300: candles)
    monkeypatch.setattr(pages, "get_worker_status", lambda api_url: worker_status)

    monkeypatch.setattr(
        pages,
        "_build_trade_reasoning",
        lambda ai, candles, confidence_threshold=55.0, conviction_threshold=0.35, agreement_threshold=0.30: {
            "go_nogo": "NO-GO",
            "go_nogo_reason": "test",
            "headline": "test",
            "actions": [],
            "gates": [],
            "factors": pd.DataFrame(),
            "composite": 0.0,
        },
    )
    monkeypatch.setattr(
        pages,
        "get_risk_preview",
        lambda api_url, payload: {
            "collateral_bucket_exposure_pct": 0.10,
            "collateral_bucket_limit_pct": 0.60,
            "trade_collateral_asset": "BTC",
        },
    )
    monkeypatch.setattr(pages, "_chart", lambda df, ai, live_price: None)
    monkeypatch.setattr(pages, "render_execution_debug", lambda signal: None)
    return fake_st


def test_render_dashboard_shows_entry_gate_failure_snapshot(monkeypatch) -> None:
    pages = _load_pages_module()
    entry_snapshot = {
        "side": "sell",
        "timestamp": "2026-03-05T06:00:00+00:00",
        "confidence_pct": 12.34,
        "composite": -0.104,
        "confidence_gate": False,
        "conviction_gate": True,
        "trend_gate": False,
        "vol_gate": True,
        "pattern_gate": True,
        "agreement_gate": True,
        "direction_gate": True,
    }
    fake_st = _seed_dashboard_mocks(
        monkeypatch,
        pages,
        {
            "is_running": True,
            "symbol": "PI_XBTUSD",
            "signal_count": 534,
            "execution_count": 0,
            "last_decision_reason": "entry_gate_failed:confidence",
            "last_entry_gate_snapshot": entry_snapshot,
        },
    )

    stream = SimpleNamespace(
        latest=SimpleNamespace(price=72018.0, ts="2026-03-05T06:00:00+00:00")
    )
    pages.render_dashboard("http://localhost:8000", stream, risk_preview={})

    confidence_metrics = [value for label, value in fake_st.metrics if label == "Confidence %"]
    assert confidence_metrics
    assert confidence_metrics[-1] == "12.34%"

    assert any(
        "Execution gate confidence source: worker snapshot (12.34%)." in line
        for line in fake_st.captions
    )
    assert any("Entry gate snapshot: conf=12.34% | composite=-0.104" in line for line in fake_st.captions)
    assert any("Failed gates: confidence, trend" in line for line in fake_st.captions)


def test_render_dashboard_pass_path_uses_snapshot_confidence_without_failed_gate_caption(monkeypatch) -> None:
    pages = _load_pages_module()
    entry_snapshot = {
        "side": "buy",
        "timestamp": "2026-03-05T06:00:00+00:00",
        "confidence_pct": 77.70,
        "composite": 0.420,
        "confidence_gate": True,
        "conviction_gate": True,
        "trend_gate": True,
        "vol_gate": True,
        "pattern_gate": True,
        "agreement_gate": True,
        "direction_gate": True,
    }
    fake_st = _seed_dashboard_mocks(
        monkeypatch,
        pages,
        {
            "is_running": True,
            "symbol": "PI_XBTUSD",
            "signal_count": 12,
            "execution_count": 3,
            "last_decision_reason": "entry_gate_pass",
            "last_entry_gate_snapshot": entry_snapshot,
        },
    )

    stream = SimpleNamespace(
        latest=SimpleNamespace(price=72018.0, ts="2026-03-05T06:00:00+00:00")
    )
    pages.render_dashboard("http://localhost:8000", stream, risk_preview={})

    confidence_metrics = [value for label, value in fake_st.metrics if label == "Confidence %"]
    assert confidence_metrics
    assert confidence_metrics[-1] == "77.70%"
    assert not any("Failed gates:" in line for line in fake_st.captions)


def test_render_dashboard_handles_sparse_snapshot_fields_without_crashing(monkeypatch) -> None:
    pages = _load_pages_module()
    sparse_snapshot = {
        "side": "sell",
        "confidence_pct": None,
        "composite": None,
        "confidence_gate": None,
        "pattern_gate": True,
    }
    fake_st = _seed_dashboard_mocks(
        monkeypatch,
        pages,
        {
            "is_running": True,
            "symbol": "PI_XBTUSD",
            "signal_count": 90,
            "execution_count": 0,
            "last_decision_reason": "entry_gate_failed:confidence",
            "last_entry_gate_snapshot": sparse_snapshot,
        },
    )

    stream = SimpleNamespace(
        latest=SimpleNamespace(price=72018.0, ts="2026-03-05T06:00:00+00:00")
    )
    pages.render_dashboard("http://localhost:8000", stream, risk_preview={})

    confidence_metrics = [value for label, value in fake_st.metrics if label == "Confidence %"]
    assert confidence_metrics
    # No valid worker confidence in sparse snapshot; should fall back to analytics confidence.
    assert confidence_metrics[-1] == "99.00%"


def test_render_dashboard_warns_when_entry_snapshot_is_stale(monkeypatch) -> None:
    pages = _load_pages_module()
    monkeypatch.setenv("UI_ENTRY_GATE_STALE_SEC", "60")

    stale_snapshot = {
        "side": "sell",
        "timestamp": "2026-03-05T00:00:00+00:00",
        "confidence_pct": 25.0,
        "composite": -0.200,
        "confidence_gate": False,
    }
    fake_st = _seed_dashboard_mocks(
        monkeypatch,
        pages,
        {
            "is_running": True,
            "symbol": "PI_XBTUSD",
            "signal_count": 30,
            "execution_count": 0,
            "last_decision_reason": "entry_gate_failed:confidence",
            "last_entry_gate_snapshot": stale_snapshot,
        },
    )

    stream = SimpleNamespace(
        latest=SimpleNamespace(price=72018.0, ts="2026-03-05T06:00:00+00:00")
    )
    pages.render_dashboard("http://localhost:8000", stream, risk_preview={})

    assert any("Entry gate snapshot age:" in line for line in fake_st.captions)
    assert any("Entry gate snapshot appears stale" in line for line in fake_st.warnings)


def test_render_dashboard_rerun_replaces_snapshot_confidence(monkeypatch) -> None:
    pages = _load_pages_module()
    state = {
        "is_running": True,
        "symbol": "PI_XBTUSD",
        "signal_count": 1,
        "execution_count": 0,
        "last_decision_reason": "entry_gate_failed:confidence",
        "last_entry_gate_snapshot": {"confidence_pct": 11.0, "confidence_gate": False},
    }
    fake_st = _seed_dashboard_mocks(monkeypatch, pages, state)
    stream = SimpleNamespace(
        latest=SimpleNamespace(price=72018.0, ts="2026-03-05T06:00:00+00:00")
    )

    pages.render_dashboard("http://localhost:8000", stream, risk_preview={})
    state["last_entry_gate_snapshot"] = {"confidence_pct": 66.0, "confidence_gate": True}
    state["last_decision_reason"] = "entry_gate_pass"
    pages.render_dashboard("http://localhost:8000", stream, risk_preview={})

    confidence_metrics = [value for label, value in fake_st.metrics if label == "Confidence %"]
    assert confidence_metrics[-2:] == ["11.00%", "66.00%"]
