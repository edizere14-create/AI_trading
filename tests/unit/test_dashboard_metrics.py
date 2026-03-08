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


def test_all_in_one_active_trades_backfill_mark_with_ticker(monkeypatch) -> None:
    class _FakeExchange:
        def fetch_positions(self):
            return [
                {
                    "symbol": "BTC/USD:USD",
                    "side": "long",
                    "contracts": 2.0,
                    "entryPrice": 100.0,
                }
            ]

        def fetch_ticker(self, symbol: str):
            return {"last": 90.0}

    monkeypatch.setattr(data_client, "_build_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(data_client, "_all_in_one_enabled", lambda _: True)
    monkeypatch.setattr(data_client, "_contracts_to_display_quantity", lambda symbol, contracts, price, contract_size: abs(float(contracts)))

    trades = data_client.get_active_trades("all-in-one")

    assert not trades.empty
    assert float(trades.loc[0, "current_price"]) == 90.0
    assert float(trades.loc[0, "unrealized_pnl"]) == -20.0


def test_backend_metrics_preserve_zero_gate_confidence(monkeypatch) -> None:
    def _fake_get_json(url: str, params=None):
        if url.endswith("/momentum/status"):
            return {
                "ai": {"bias": "SELL", "confidence": 92.0},
                "last_entry_gate_snapshot": {"confidence_pct": 0.0},
            }
        if url.endswith("/risk/status"):
            return {
                "account_balance": 5000.0,
                "total_pnl": 0.0,
                "daily_pnl": 0.0,
                "exposure_pct": 0.0,
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)
    monkeypatch.setattr(data_client, "get_active_trades", lambda api_url: pd.DataFrame())

    metrics = data_client.get_metrics("http://127.0.0.1:8000")
    assert metrics["confidence"] == 0.0


def test_all_in_one_metrics_preserve_zero_gate_confidence(monkeypatch) -> None:
    class _FakeExchange:
        def fetch_ticker(self, symbol: str):
            return {"last": 72000.0}

        def fetch_balance(self):
            return {"total": {"USD": 5000.0}}

    monkeypatch.setattr(data_client, "_all_in_one_enabled", lambda _: True)
    monkeypatch.setattr(data_client, "_build_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(
        data_client,
        "get_ai_insight",
        lambda api_url: {"bias": "BUY", "confidence": 95.0},
    )
    monkeypatch.setattr(
        data_client,
        "get_worker_status",
        lambda api_url: {"last_entry_gate_snapshot": {"confidence_pct": 0.0}},
    )
    monkeypatch.setattr(data_client, "get_active_trades", lambda api_url: pd.DataFrame())

    metrics = data_client.get_metrics("all-in-one")
    assert metrics["confidence"] == 0.0


def test_portfolio_computes_weight_pct_from_active_trades(monkeypatch) -> None:
    monkeypatch.setattr(
        data_client,
        "get_active_trades",
        lambda _: pd.DataFrame(
            [
                {"symbol": "A", "quantity": 1.0, "current_price": 100.0},
                {"symbol": "B", "quantity": 1.0, "current_price": 300.0},
            ]
        ),
    )

    pf = data_client.get_portfolio("http://127.0.0.1:8000")

    assert not pf.empty
    weights = pf.set_index("symbol")["weight_pct"].to_dict()
    assert weights["A"] == 25.0
    assert weights["B"] == 75.0


def test_get_ai_insight_forwards_selected_symbol_to_analytics(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake_get_json(url: str, params=None):
        seen["url"] = url
        seen["params"] = params
        return {
            "bias": "BUY",
            "confidence": 70.0,
            "volatility_forecast": 0.2,
            "pattern_summary": "ok",
            "why": "test",
            "signals": [],
        }

    monkeypatch.setattr(data_client, "_all_in_one_enabled", lambda _: False)
    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)

    result = data_client.get_ai_insight("http://127.0.0.1:8000", symbol="PI_ETHUSD")

    assert result["bias"] == "BUY"
    assert str(seen["url"]).endswith("/momentum/analytics")
    assert seen["params"] == {"symbol": "PF_ETHUSD"}


def test_get_candles_forwards_selected_symbol_to_backend_ohlcv(monkeypatch) -> None:
    seen_calls: list[tuple[str, dict[str, object] | None]] = []

    def _fake_get_json(url: str, params=None):
        seen_calls.append((url, params))
        if url.endswith("/data/kraken/ohlcv"):
            return {
                "candles": [
                    {
                        "timestamp": "2026-03-05T06:00:00Z",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10.0,
                    },
                    {
                        "timestamp": "2026-03-05T06:01:00Z",
                        "open": 100.5,
                        "high": 101.2,
                        "low": 100.1,
                        "close": 100.9,
                        "volume": 11.0,
                    },
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(data_client, "_all_in_one_enabled", lambda _: False)
    monkeypatch.setattr(data_client, "_get_json", _fake_get_json)

    df = data_client.get_candles(
        "http://127.0.0.1:8000",
        symbol="PI_SOLUSD",
        limit=2,
        timeframe="1m",
    )

    assert not df.empty
    assert len(seen_calls) >= 1
    first_url, first_params = seen_calls[0]
    assert first_url.endswith("/data/kraken/ohlcv")
    assert first_params == {"symbol": "PF_SOLUSD", "timeframe": "1m", "limit": 50}


def test_to_kraken_symbol_mapping_coverage() -> None:
    assert data_client._to_kraken_symbol("PF_XBTUSD") == "BTC/USD:USD"
    assert data_client._to_kraken_symbol("PF_ETHUSD") == "ETH/USD:USD"
    assert data_client._to_kraken_symbol("PF_SOLUSD") == "SOL/USD:USD"
    assert data_client._to_kraken_symbol("PF_AVAXUSD") == "AVAX/USD:USD"
    assert data_client._to_kraken_symbol("PF_ADAUSD") == "ADA/USD:USD"
