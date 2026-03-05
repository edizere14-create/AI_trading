import sys
import types

import pandas as pd

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")

    async def _connect_stub(*args, **kwargs):
        raise RuntimeError("asyncpg stub")

    asyncpg_stub.connect = _connect_stub  # type: ignore[attr-defined]
    sys.modules["asyncpg"] = asyncpg_stub

from app.ui import data_client


def test_backend_metrics_fallback_to_unrealized_when_reported_pnl_is_zero(monkeypatch) -> None:
    def _fake_get_json(url: str, params=None):
        if url.endswith("/momentum/status"):
            return {"ai": {"bias": "BUY", "confidence": 87.5}}
        if url.endswith("/risk/status"):
            return {
                "account_balance": 5000.0,
                "total_pnl": 0.0,
                "daily_pnl": 0.0,
                "exposure_pct": 0.0,
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)
    monkeypatch.setattr(
        data_client,
        "get_active_trades",
        lambda api_url: pd.DataFrame(
            [
                {
                    "symbol": "BTC/USD:USD",
                    "unrealized_pnl": 42.5,
                    "notional": 1000.0,
                }
            ]
        ),
    )

    metrics = data_client.get_metrics("http://127.0.0.1:8000")

    assert metrics["daily_pnl"] == 42.5
    assert metrics["risk_exposure"] == 20.0


def test_backend_metrics_keep_reported_values_when_non_zero(monkeypatch) -> None:
    def _fake_get_json(url: str, params=None):
        if url.endswith("/momentum/status"):
            return {"ai": {"bias": "SELL", "confidence": 61.0}}
        if url.endswith("/risk/status"):
            return {
                "account_balance": 5000.0,
                "total_pnl": 15.0,
                "daily_pnl": 15.0,
                "exposure_pct": 12.0,
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)
    monkeypatch.setattr(
        data_client,
        "get_active_trades",
        lambda api_url: pd.DataFrame(
            [
                {
                    "symbol": "BTC/USD:USD",
                    "unrealized_pnl": 8.0,
                    "notional": 1200.0,
                }
            ]
        ),
    )

    metrics = data_client.get_metrics("http://127.0.0.1:8000")

    assert metrics["daily_pnl"] == 15.0
    assert metrics["risk_exposure"] == 12.0


def test_active_trades_backfills_mark_and_unrealized_from_worker_status(monkeypatch) -> None:
    def _fake_get_json(url: str, params=None):
        if url.endswith("/risk/positions"):
            return {
                "positions": [
                    {
                        "symbol": "BTC/USD:USD",
                        "side": "buy",
                        "quantity": 2.0,
                        "entry_price": 100.0,
                    }
                ]
            }
        if url.endswith("/momentum/status"):
            return {"last_price": 90.0}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)

    df = data_client.get_active_trades("http://127.0.0.1:8000")
    assert not df.empty
    assert float(df.loc[0, "current_price"]) == 90.0
    assert float(df.loc[0, "unrealized_pnl"]) == -20.0
    assert float(df.loc[0, "notional"]) == 180.0


def test_metrics_use_backfilled_unrealized_when_risk_status_is_zero(monkeypatch) -> None:
    def _fake_get_json(url: str, params=None):
        if url.endswith("/momentum/status"):
            return {"last_price": 90.0, "ai": {"bias": "BUY", "confidence": 70.0}}
        if url.endswith("/risk/status"):
            return {
                "account_balance": 1000.0,
                "total_pnl": 0.0,
                "daily_pnl": 0.0,
                "exposure_pct": 0.0,
            }
        if url.endswith("/risk/positions"):
            return {
                "positions": [
                    {
                        "symbol": "BTC/USD:USD",
                        "side": "buy",
                        "quantity": 2.0,
                        "entry_price": 100.0,
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)

    metrics = data_client.get_metrics("http://127.0.0.1:8000")
    assert metrics["daily_pnl"] == -20.0
    assert metrics["risk_exposure"] == 18.0
