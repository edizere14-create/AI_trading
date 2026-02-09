from datetime import datetime, timezone

from app.models.signals import TradingSignal, AdvancedTradingSignal, SignalType
from app.schemas.market_data import PriceResponse, CandleResponse
from app.schemas.strategy import StrategyRunRequest, Trade, StrategyRunResult

def main() -> None:
    print("Pydantic Debug Script\n")

    # Signals
    print("Signals:")
    signal = TradingSignal(
        signal=SignalType.BUY,
        symbol="BTC/USD",
        timestamp=datetime.now(timezone.utc),
        price=50000.0,
        confidence=0.85,
    )
    print("  TradingSignal OK:", signal)

    adv_signal = AdvancedTradingSignal(
        signal=SignalType.SELL,
        symbol="ETH/USD",
        timestamp=datetime.now(timezone.utc),
        price=3000.0,
        confidence=0.75,
        quantity=1.0,
        stop_loss=2800.0,
        take_profit=3200.0,
        tags=["rsi"],
    )
    print("  AdvancedTradingSignal OK:", adv_signal.to_dict())

    # Market data
    print("\nMarket Data:")
    price = PriceResponse(
        symbol="BTC/USD",
        price=50000.0,
        timestamp=datetime.now(timezone.utc),
    )
    print("  PriceResponse OK:", price.model_dump())

    candle = CandleResponse(
        timestamp=datetime.now(timezone.utc),
        open=50000.0,
        high=51000.0,
        low=49500.0,
        close=50500.0,
        volume=123.45,
    )
    print("  CandleResponse OK:", candle.model_dump())

    # Strategy schema
    print("\nStrategy:")
    req = StrategyRunRequest(
        strategy_code="print('hello')",
        symbol="BTC/USD",
        timeframe="1h",
        start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end=datetime(2025, 1, 2, tzinfo=timezone.utc),
        initial_capital=10_000,
    )
    print("  StrategyRunRequest OK:", req.model_dump())

    trade = Trade(
        timestamp=datetime.now(timezone.utc),
        side="buy",
        price=50000.0,
        size=0.1,
    )
    result = StrategyRunResult(
        total_return=0.12,
        max_drawdown=0.05,
        sharpe=1.5,
        trades=[trade],
    )
    print("  StrategyRunResult OK:", result.model_dump())

if __name__ == "__main__":
    main()