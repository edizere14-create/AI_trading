import backtrader as bt
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

class BacktestStrategy(bt.Strategy):
    """Base backtesting strategy with signal integration"""
    
    params = {
        'take_profit': 0.02,
        'stop_loss': 0.01,
        'position_size': 0.1,
    }
    
    def __init__(self):
        self.signals = None
        self.trades_log = []
        
    def next(self):
        if not self.position:
            if self.signals[0] > 0.5:  # Buy signal
                size = self.broker.getcash() * self.params['position_size'] / self.data.close[0]
                self.buy(size=size)
                
        else:
            if self.signals[0] < 0.5:  # Sell signal
                self.sell(size=self.position.size)
    
    def notify_trade(self, trade):
        if trade.isclosed:
            self.trades_log.append({
                'date': bt.num2date(trade.barlen),
                'entry': trade.barlen,
                'exit': trade.barlen,
                'pnl': trade.pnl,
                'pnlpercent': trade.pnlpercent,
            })


class BacktestService:
    """Enterprise backtesting with Backtrader"""
    
    def __init__(self, db_session):
        self.db_session = db_session
        self.cerebro = None
        
    def run_backtest(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        data: pd.DataFrame,
        signals: List[float],
        initial_cash: float = 10000.0,
        commission: float = 0.001,
    ) -> Dict:
        """
        Run accurate backtest with signal integration
        
        Args:
            symbol: Trading pair (e.g., 'XRPUSD')
            start_date: Backtest start
            end_date: Backtest end
            data: OHLCV DataFrame
            signals: ML-generated signals
            initial_cash: Starting capital
            commission: Trading fee
            
        Returns:
            Backtest results with metrics
        """
        try:
            self.cerebro = bt.Cerebro()
            self.cerebro.broker.setcash(initial_cash)
            self.cerebro.broker.setcommission(commission=commission)
            
            # Prepare data feed
            data_feed = self._prepare_datafeed(data, symbol)
            self.cerebro.adddata(data_feed)
            
            # Add strategy with signals
            strategy_class = self._create_signal_strategy(signals)
            self.cerebro.addstrategy(strategy_class)
            
            # Add analyzers for comprehensive metrics
            self.cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
            self.cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
            self.cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
            self.cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
            
            # Run backtest
            logger.info(f"Starting backtest for {symbol} ({start_date} to {end_date})")
            results = self.cerebro.run()
            strat = results[0]
            
            # Extract metrics
            metrics = self._extract_metrics(strat, initial_cash)
            metrics['symbol'] = symbol
            metrics['start_date'] = start_date
            metrics['end_date'] = end_date
            
            logger.info(f"Backtest complete. Sharpe: {metrics['sharpe_ratio']:.2f}")
            return metrics
            
        except Exception as e:
            logger.error(f"Backtest failed: {str(e)}")
            raise
    
    def _prepare_datafeed(self, data: pd.DataFrame, symbol: str) -> bt.feeds.PandasData:
        """Convert DataFrame to Backtrader feed"""
        data_copy = data.copy()
        data_copy['datetime'] = pd.to_datetime(data_copy.index)
        data_copy.set_index('datetime', inplace=True)
        
        return bt.feeds.PandasData(
            dataname=data_copy,
            fromdate=data_copy.index[0],
            todate=data_copy.index[-1],
        )
    
    def _create_signal_strategy(self, signals: List[float]):
        """Dynamically create strategy with signals"""
        class SignalStrategy(BacktestStrategy):
            def __init__(self):
                super().__init__()
                self.signal_array = signals
                self.signal_idx = 0
            
            def next(self):
                if self.signal_idx < len(self.signal_array):
                    sig = self.signal_array[self.signal_idx]
                    self.signals = [sig]
                    super().next()
                    self.signal_idx += 1
        
        return SignalStrategy
    
    def _extract_metrics(self, strategy, initial_cash: float) -> Dict:
        """Extract comprehensive backtest metrics"""
        analyzers = strategy.analyzers
        
        final_value = strategy.broker.getvalue()
        total_return = (final_value - initial_cash) / initial_cash
        
        sharpe = analyzers.sharpe.get_analysis().get('sharperatio', 0)
        drawdown = analyzers.drawdown.get_analysis().get('max', {}).get('drawdown', 0)
        
        trades_analysis = analyzers.trades.get_analysis()
        total_trades = trades_analysis.get('total', {}).get('total', 0)
        win_rate = trades_analysis.get('won', {}).get('total', 0) / max(total_trades, 1)
        
        return {
            'final_value': final_value,
            'total_return': total_return,
            'sharpe_ratio': sharpe,
            'max_drawdown': drawdown,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': self._calculate_profit_factor(trades_analysis),
        }
    
    def _calculate_profit_factor(self, trades_analysis: Dict) -> float:
        """Calculate profit factor (gross profit / gross loss)"""
        gross_profit = trades_analysis.get('won', {}).get('pnl', {}).get('total', 0)
        gross_loss = abs(trades_analysis.get('lost', {}).get('pnl', {}).get('total', 0))
        
        return gross_profit / max(gross_loss, 0.001)
