import datetime as dt
import pytest

from app.services.data_service import get_live_price


class _MockResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _MockAsyncClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params):
        return _MockResponse(self._payload)


@pytest.mark.asyncio
async def test_get_live_price_btc(monkeypatch) -> None:
    payload = {"bitcoin": {"usd": 42000.5, "usd_24h_change": 1.2}}

    def _client_factory(*args, **kwargs):
        return _MockAsyncClient(payload)

    monkeypatch.setattr("app.services.data_service.httpx.AsyncClient", _client_factory)

    result = await get_live_price("BTC")

    assert result.symbol == "BTC"
    assert result.price == 42000.5
    assert isinstance(result.timestamp, dt.datetime)


@pytest.mark.asyncio
async def test_get_live_price_default_symbol(monkeypatch) -> None:
    payload = {"bitcoin": {"usd": 123.45, "usd_24h_change": -0.5}}

    def _client_factory(*args, **kwargs):
        return _MockAsyncClient(payload)

    monkeypatch.setattr("app.services.data_service.httpx.AsyncClient", _client_factory)

    result = await get_live_price("UNKNOWN")

    assert result.symbol == "UNKNOWN"
    assert result.price == 123.45
    assert isinstance(result.timestamp, dt.datetime)
