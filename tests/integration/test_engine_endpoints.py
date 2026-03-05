import pandas as pd
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.data_service import DataService


pytestmark = pytest.mark.integration


def _mock_ohlcv(limit: int = 300) -> pd.DataFrame:
    periods = max(120, min(limit, 500))
    ts = pd.date_range("2026-01-01", periods=periods, freq="h", tz="UTC")

    base = pd.Series(range(periods), dtype=float) + 100.0
    close = base + (base * 0.002)
    open_ = close * 0.999
    high = close * 1.002
    low = close * 0.998
    volume = pd.Series([1000.0 + (i % 25) for i in range(periods)], dtype=float)

    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_engine_endpoints_flow(monkeypatch) -> None:
    async def fake_get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200, exchange_id: str | None = None):
        return _mock_ohlcv(limit=limit)

    monkeypatch.setattr(DataService, "get_ohlcv", fake_get_ohlcv)

    client = TestClient(app)

    signal_resp = client.get(
        "/strategies/signal",
        params={
            "symbol": "PI_XBTUSD",
            "mode": "balanced",
            "timeframes": "1h,4h",
            "lookback": 180,
        },
    )
    assert signal_resp.status_code == 200
    signal = signal_resp.json()
    assert signal["action"] in {"buy", "sell", "hold"}
    assert "multi_timeframe" in signal
    assert len(signal["multi_timeframe"]) >= 1

    risk_payload = {
        "equity": 10000,
        "trade": {
            "symbol": "PI_XBTUSD",
            "side": "buy",
            "entry_price": 100,
            "stop_price": 98,
            "quantity": 10,
            "leverage": 2.0,
        },
        "open_positions": [],
    }
    risk_resp = client.post("/risk/check", json=risk_payload)
    assert risk_resp.status_code == 200
    risk = risk_resp.json()
    assert "approved" in risk
    assert "reason" in risk
    assert "max_position_size" in risk

    backtest_resp = client.get(
        "/backtest/analytics",
        params={"days": 90, "symbol": "PI_XBTUSD", "timeframe": "1h"},
    )
    assert backtest_resp.status_code == 200
    analytics = backtest_resp.json()
    assert "total_return_pct" in analytics
    assert "max_drawdown_pct" in analytics
    assert "equity_curve" in analytics
    assert "monthly_performance" in analytics
