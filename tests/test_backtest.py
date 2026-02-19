import pytest
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from app.services.backtest_service import BacktestService
from app.services.ml_signal_service import MLSignalService


@pytest.fixture
def sample_ohlcv_data():
    """Generate sample OHLCV data for testing"""
    dates = pd.date_range(start='2025-01-01', periods=100, freq='D')
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(100) * 2)
    
    return pd.DataFrame({
        'open': close + np.random.randn(100) * 0.5,
        'high': close + np.abs(np.random.randn(100) * 1),
        'low': close - np.abs(np.random.randn(100) * 1),
        'close': close,
        'volume': np.random.randint(1000000, 5000000, 100),
    }, index=dates)


@pytest.fixture
def ml_signal_service():
    """Initialize ML signal service"""
    return MLSignalService(lookback_period=20)


class TestMLSignalService:
    """Test ML signal generation"""
    
    def test_prepare_features(self, ml_signal_service, sample_ohlcv_data):
        """Test feature preparation"""
        X, y = ml_signal_service.prepare_features(sample_ohlcv_data)
        
        assert X.shape[0] == len(sample_ohlcv_data)
        assert X.shape[1] > 0  # Multiple features
        assert len(y) == len(sample_ohlcv_data)
    
    def test_rsi_calculation(self, ml_signal_service, sample_ohlcv_data):
        """Test RSI calculation"""
        rsi = ml_signal_service._calculate_rsi(sample_ohlcv_data['close'], 14)
        
        assert len(rsi) == len(sample_ohlcv_data)
        assert np.all((rsi >= 0) & (rsi <= 1))
    
    def test_macd_calculation(self, ml_signal_service, sample_ohlcv_data):
        """Test MACD calculation"""
        macd = ml_signal_service._calculate_macd(sample_ohlcv_data['close'])
        
        assert len(macd) == len(sample_ohlcv_data)
    
    def test_signal_generation(self, ml_signal_service, sample_ohlcv_data):
        """Test signal generation without model"""
        signals = ml_signal_service.generate_signals(sample_ohlcv_data)
        
        assert len(signals) == len(sample_ohlcv_data)
        assert all(0 <= s <= 1 for s in signals)


class TestBacktestService:
    """Test backtesting (requires mocking DB)"""
    
    def test_backtest_initialization(self):
        """Test backtest service initialization"""
        from unittest.mock import Mock
        mock_db = Mock()
        service = BacktestService(mock_db)
        
        assert service.db_session is not None


class TestGridTrading:
    """Test grid trading strategy"""
    
    def test_grid_initialization(self):
        """Test grid initialization"""
        from app.strategies.grid_trading import GridTradingStrategy
        
        strategy = GridTradingStrategy(
            symbol="XRPUSD",
            grid_levels=10,
            grid_amount=1000,
            profit_percentage=0.5,
            upper_price=3.0,
            lower_price=2.0
        )
        
        orders = strategy.initialize_grid(current_price=2.5)
        assert len(orders) == 10
    
    def test_grid_order_fill(self):
        """Test grid order fill handling"""
        from app.strategies.grid_trading import GridTradingStrategy
        
        strategy = GridTradingStrategy(
            symbol="XRPUSD",
            grid_levels=5,
            grid_amount=1000,
            upper_price=3.0,
            lower_price=2.0
        )
        
        orders = strategy.initialize_grid(2.5)
        buy_order = next((o for o in orders if o['side'] == 'buy'), None)
        
        offset = strategy.on_fill(buy_order, 2.45)
        assert offset['side'] == 'sell'
        assert offset['price'] > buy_order['price']


class TestDCAStrategy:
    """Test DCA strategy"""
    
    def test_dca_initialization(self):
        """Test DCA initialization"""
        from app.strategies.dca_strategy import DCAStrategy
        
        strategy = DCAStrategy(
            symbol="XRPUSD",
            investment_amount=1000,
            interval_days=1
        )
        
        assert strategy.symbol == "XRPUSD"
        assert strategy.investment_amount == 1000
    
    def test_dca_buy_execution(self):
        """Test DCA buy execution"""
        from app.strategies.dca_strategy import DCAStrategy
        from datetime import datetime
        
        strategy = DCAStrategy(
            symbol="XRPUSD",
            investment_amount=1000,
            interval_days=1
        )
        strategy.next_buy_date = datetime.now()  # Force immediate buy
        
        order = strategy.execute_buy(current_price=2.5)
        
        assert order['symbol'] == "XRPUSD"
        assert order['side'] == 'buy'
        assert order['price'] == 2.5
        assert order['quantity'] == 1000 / 2.5
    
    def test_dca_statistics(self):
        """Test DCA statistics calculation"""
        from app.strategies.dca_strategy import DCAStrategy
        from datetime import datetime
        
        strategy = DCAStrategy(
            symbol="XRPUSD",
            investment_amount=1000,
            interval_days=1
        )
        strategy.next_buy_date = datetime.now()
        
        strategy.execute_buy(2.5)
        stats = strategy.get_statistics(3.0)
        
        assert stats['total_invested'] == 1000
        assert stats['average_cost'] == 2.5
        assert stats['unrealized_pnl'] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])