import pytest
from app.brokers.paper_trading import PaperTradingBroker

@pytest.mark.asyncio
async def test_paper_broker_order_status_returns_fill_info():
    broker = PaperTradingBroker()

    result = await broker.place_order(
        symbol="BTCUSD",
        side="buy",
        quantity=0.1,
        price=None,
        order_type="market",
        order_kind="taker",
        expected_price=43500.0,
    )

    assert result["status"] == "filled"
    order_id = result.get("order_id")
    assert order_id

    status = await broker.get_order_status(order_id)

    assert status["status"] == "filled"
    assert status.get("filled_quantity") == 0.1
    assert status.get("avg_fill_price") is not None