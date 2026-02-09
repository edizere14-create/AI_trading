"""Integration test for trading app fixes."""
import asyncio
from datetime import datetime, timezone
from app.models.signals import SignalType, TradingSignal, AdvancedTradingSignal
from app.strategies.rsi_strategy import RSIStrategy
from app.services.strategy_manager import StrategyManager

async def test_signals() -> None:
    """Test signal creation and validation."""
    print("Testing TradingSignal...")
    
    try:
        signal = TradingSignal(
            signal=SignalType.BUY,
            symbol='BTC/USD',
            timestamp=datetime.now(timezone.utc),
            price=50000.0,
            confidence=0.85,
            reason="Test signal"
        )
        print(f"✓ TradingSignal created: {signal.signal.value} at ${signal.price}")
    except Exception as e:
        print(f"✗ TradingSignal error: {e}")
    
    print("\nTesting AdvancedTradingSignal...")
    try:
        adv_signal = AdvancedTradingSignal(
            signal=SignalType.SELL,
            symbol='ETH/USD',
            timestamp=datetime.now(timezone.utc),
            price=3000.0,
            confidence=0.75,
            quantity=1.0,
            stop_loss=2700.0,
            take_profit=3300.0,
            tags=['rsi', 'overbought']
        )
        print(f"✓ AdvancedTradingSignal created: {adv_signal.signal.value}")
        print(f"  Signal dict: {adv_signal.to_dict()}")
    except Exception as e:
        print(f"✗ AdvancedTradingSignal error: {e}")

async def test_rsi_strategy() -> None:
    """Test RSI strategy."""
    print("\n" + "="*50)
    print("Testing RSI Strategy...")
    
    strategy = RSIStrategy("BTC/USD", overbought=70, oversold=30)
    
    test_cases = [
        {"rsi": 25, "price": 50000, "expected": "STRONG_BUY"},
        {"rsi": 35, "price": 50000, "expected": "BUY"},
        {"rsi": 50, "price": 50000, "expected": "HOLD"},
        {"rsi": 65, "price": 50000, "expected": "SELL"},
        {"rsi": 75, "price": 50000, "expected": "STRONG_SELL"},
    ]
    
    for case in test_cases:
        data = {"rsi": case["rsi"], "price": case["price"]}
        signal = await strategy.analyze(data)
        
        if signal:
            status = "✓" if signal.signal.value.upper() == case["expected"] else "✗"
            print(f"{status} RSI {case['rsi']}: {signal.signal.value} (expected: {case['expected']})")
            print(f"   Reason: {signal.reason}")
            if signal.stop_loss:
                print(f"   Stop Loss: ${signal.stop_loss:.2f}, Take Profit: ${signal.take_profit:.2f}")
        else:
            print(f"✗ RSI {case['rsi']}: No signal generated")

async def test_strategy_manager() -> None:
    """Test strategy manager."""
    print("\n" + "="*50)
    print("Testing Strategy Manager...")
    
    manager = StrategyManager()
    manager.create_rsi_strategy("BTC/USD")
    manager.create_rsi_strategy("ETH/USD")
    
    print(f"✓ Registered {len(manager.strategies)} strategies")
    
    # Test analysis
    data = {"rsi": 25, "price": 50000}
    signals = await manager.analyze_all("BTC/USD", data)
    
    if signals:
        print(f"✓ Generated {len(signals)} signals for BTC/USD")
        for signal in signals:
            print(f"  - {signal.signal.value}: {signal.reason}")
    else:
        print("✗ No signals generated")

async def main() -> None:
    """Run all integration tests."""
    print("="*50)
    print("AI Trading App Integration Tests")
    print("="*50)
    
    await test_signals()
    await test_rsi_strategy()
    await test_strategy_manager()
    
    print("\n" + "="*50)
    print("Integration tests completed!")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())