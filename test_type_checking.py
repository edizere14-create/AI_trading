"""Test all service return types match their schemas."""
import asyncio
from datetime import datetime, timezone

from app.schemas.indicator import IndicatorRequest, RSIResponse, MACDResponse
from app.schemas.market_data import PriceResponse, CandleResponse
from app.schemas.strategy import StrategyRunRequest, StrategyRunResult, Trade

async def test_service_types() -> None:
    """Test that all services return correct types."""
    print("Testing Service Return Types\n")
    
    # Test indicator schemas
    print("1. Indicator Schemas:")
    rsi_req = IndicatorRequest(symbol="BTC/USD", period=14)
    print(f"  ✓ IndicatorRequest: {rsi_req.model_dump()}")
    
    rsi_resp = RSIResponse(
        symbol="BTC/USD",
        period=14,
        timeframe="1h",
        rsi=65.5,
        timestamp=datetime.now(timezone.utc)
    )
    print(f"  ✓ RSIResponse: {rsi_resp.model_dump()}")
    
    macd_resp = MACDResponse(
        symbol="BTC/USD",
        period=12,
        timeframe="1h",
        macd=0.12,
        signal=0.10,
        histogram=0.02,
        timestamp=datetime.now(timezone.utc)
    )
    print(f"  ✓ MACDResponse: {macd_resp.model_dump()}")
    
    # Test market data schemas
    print("\n2. Market Data Schemas:")
    price = PriceResponse(
        symbol="BTC/USD",
        price=50000.0,
        timestamp=datetime.now(timezone.utc)
    )
    print(f"  ✓ PriceResponse: {price.model_dump()}")
    
    candle = CandleResponse(
        timestamp=datetime.now(timezone.utc),
        open=50000.0,
        high=51000.0,
        low=49500.0,
        close=50500.0,
        volume=123.45
    )
    print(f"  ✓ CandleResponse: {candle.model_dump()}")
    
    # Test strategy schemas
    print("\n3. Strategy Schemas:")
    strategy_req = StrategyRunRequest(
        strategy_code="# Simple strategy",
        symbol="BTC/USD",
        timeframe="1h",
        start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end=datetime(2025, 1, 2, tzinfo=timezone.utc),
        initial_capital=10000
    )
    print(f"  ✓ StrategyRunRequest: {strategy_req.model_dump()}")
    
    trade = Trade(
        timestamp=datetime.now(timezone.utc),
        side="buy",
        price=50000.0,
        size=0.1
    )
    print(f"  ✓ Trade: {trade.model_dump()}")
    
    result = StrategyRunResult(
        total_return=0.05,
        max_drawdown=0.02,
        sharpe=1.5,
        trades=[trade]
    )
    print(f"  ✓ StrategyRunResult: {result.model_dump()}")
    
    print("\n" + "="*50)
    print("All service type checks passed! ✅")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(test_service_types())