import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.integration


def test_legacy_price_websocket_receives_realtime_event() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/price") as ws:
            msg = ws.receive_json()

    assert isinstance(msg, dict)
    assert msg.get("type") in {"heartbeat", "price"}
    assert "ts" in msg


def test_v1_market_websocket_receives_realtime_event() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market?topics=market") as ws:
            msg = ws.receive_json()

    assert isinstance(msg, dict)
    assert msg.get("type") in {"heartbeat", "price"}
    assert msg.get("topic") in {"market", None}
