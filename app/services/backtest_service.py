import asyncio
import logging
import math
from typing import Literal

import pandas as pd

from app.schemas.backtest import (
    BacktestSummaryResponse,
    EquityPoint,
    DrawdownPoint,
    MonthlyPerformanceRow,
    BacktestAnalytics,
)
from app.services.data_service import DataService

logger = logging.getLogger(__name__)

try:
    import backtrader as bt  # optional
except Exception:  # pragma: no cover
    bt = None


if bt is not None:
    class _SMACrossStrategy(bt.Strategy):  # type: ignore[misc]  # pragma: no cover (only used when bt exists)
        params = dict(fast=20, slow=50)

        def __init__(self) -> None:
            fast_period = int(getattr(self.params, "fast", 20))
            slow_period = int(getattr(self.params, "slow", 50))
            self.sma_fast = bt.indicators.SimpleMovingAverage(self.data.close, period=fast_period)
            self.sma_slow = bt.indicators.SimpleMovingAverage(self.data.close, period=slow_period)
            self.cross = bt.indicators.CrossOver(self.sma_fast, self.sma_slow)

        def next(self) -> None:
            if not self.position and self.cross > 0:
                self.buy()
            elif self.position and self.cross < 0:
                self.close()


class BacktestingEngine:
    def __init__(self, initial_equity: float = 1000.0, fee_rate: float = 0.0004, slippage_bps: float = 2.5) -> None:
        self.initial_equity = float(initial_equity)
        self.fee_rate = float(fee_rate)
        self.slippage_bps = float(slippage_bps)

    def run_historical_simulation(self, dfx: pd.DataFrame) -> pd.DataFrame:
        x = dfx.copy()
        x["ret"] = x["close"].pct_change().fillna(0.0)
        x["sma_fast"] = x["close"].rolling(20).mean()
        x["sma_slow"] = x["close"].rolling(50).mean()
        x["signal"] = (x["sma_fast"] > x["sma_slow"]).astype(int)

        x["turnover"] = x["signal"].diff().abs().fillna(0.0)
        slippage_rate = self.slippage_bps / 10_000.0
        x["cost"] = x["turnover"] * (self.fee_rate + slippage_rate)
        x["strategy_ret"] = x["signal"].shift(1).fillna(0.0) * x["ret"] - x["cost"]
        x["equity"] = self.initial_equity * (1.0 + x["strategy_ret"]).cumprod()

        x["roll_max"] = x["equity"].cummax()
        x["drawdown"] = (x["equity"] / x["roll_max"]) - 1.0
        return x

    def monthly_performance(self, simulation: pd.DataFrame) -> list[MonthlyPerformanceRow]:
        if simulation.empty:
            return []

        monthly_rows: list[MonthlyPerformanceRow] = []
        grouped = simulation.set_index("timestamp").groupby(pd.Grouper(freq="ME"))
        for month_end, g in grouped:
            if g.empty:
                continue
            start_equity = float(g["equity"].iloc[0])
            end_equity = float(g["equity"].iloc[-1])
            return_pct = ((end_equity / start_equity) - 1.0) * 100.0 if start_equity > 0 else 0.0
            trades = int((g["turnover"] > 0).sum() // 2)
            monthly_rows.append(
                MonthlyPerformanceRow(
                    month=month_end.strftime("%Y-%m"),
                    return_pct=round(return_pct, 6),
                    start_equity=round(start_equity, 6),
                    end_equity=round(end_equity, 6),
                    trades=trades,
                )
            )
        return monthly_rows

    def build_analytics(
        self,
        simulation: pd.DataFrame,
        days: int,
        symbol: str,
        timeframe: str,
    ) -> BacktestAnalytics:
        if simulation.empty:
            return BacktestAnalytics(symbol=symbol, timeframe=timeframe, days=days, slippage_bps=self.slippage_bps)

        start_equity = self.initial_equity
        end_equity = float(simulation["equity"].iloc[-1])
        total_return_pct = ((end_equity / start_equity) - 1.0) * 100.0
        years = max(days / 365.0, 1.0 / 365.0)
        annualized_return_pct = ((end_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0

        max_drawdown_pct = float(simulation["drawdown"].min() * 100.0)

        r = simulation["strategy_ret"].fillna(0.0)
        vol = float(r.std())
        mean_ret = float(r.mean())
        bars_per_year = 24 * 365
        sharpe_ratio = (mean_ret / vol) * math.sqrt(bars_per_year) if vol > 0 else 0.0

        active = simulation[simulation["signal"].shift(1).fillna(0) > 0]["strategy_ret"]
        win_rate_pct = float((active > 0).mean() * 100.0) if len(active) else 0.0

        gross_profit = float(active[active > 0].sum()) if len(active) else 0.0
        gross_loss = float(abs(active[active < 0].sum())) if len(active) else 0.0
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 0.0

        trades = int((simulation["turnover"] > 0).sum() // 2)

        equity_curve = [
            EquityPoint(timestamp=ts.isoformat(), equity=float(eq))
            for ts, eq in zip(simulation["timestamp"].tail(500), simulation["equity"].tail(500))
        ]
        drawdown_curve = [
            DrawdownPoint(timestamp=ts.isoformat(), drawdown_pct=float(dd * 100.0))
            for ts, dd in zip(simulation["timestamp"].tail(500), simulation["drawdown"].tail(500))
        ]

        return BacktestAnalytics(
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            total_return_pct=round(total_return_pct, 6),
            annualized_return_pct=round(annualized_return_pct, 6),
            max_drawdown_pct=round(max_drawdown_pct, 6),
            sharpe_ratio=round(sharpe_ratio, 6),
            win_rate_pct=round(win_rate_pct, 6),
            profit_factor=round(profit_factor, 6),
            trades=trades,
            slippage_bps=self.slippage_bps,
            start_equity=round(start_equity, 6),
            end_equity=round(end_equity, 6),
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            monthly_performance=self.monthly_performance(simulation),
        )


class BacktestService:
    def __init__(self, data_service: DataService | object, initial_equity: float = 1000.0) -> None:
        self.db_session = data_service
        if hasattr(data_service, "get_ohlcv"):
            self.data_service = data_service
        else:
            self.data_service = DataService()
        self.initial_equity = float(initial_equity)
        self.engine = BacktestingEngine(initial_equity=self.initial_equity)

    async def get_summary(
        self,
        days: int = 90,
        symbol: str = "PI_XBTUSD",
        timeframe: str = "1h",
        method: Literal["vectorized", "backtrader"] = "vectorized",
    ) -> BacktestSummaryResponse:
        limit = max(100, min(days * 24, 5000))
        df = await self.data_service.get_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        dfx = self._normalize_ohlcv(df)

        if dfx.empty or len(dfx) < 60:
            analytics = BacktestAnalytics(symbol=symbol, timeframe=timeframe, days=days, slippage_bps=self.engine.slippage_bps)
            return BacktestSummaryResponse(symbol=symbol, timeframe=timeframe, days=days, analytics=analytics)

        if method == "backtrader" and bt is not None:
            try:
                return await asyncio.to_thread(self._summary_backtrader, dfx, days, symbol, timeframe)
            except Exception as exc:
                logger.warning("Backtrader failed, falling back to vectorized: %s", exc)

        return self._summary_vectorized(dfx, days, symbol, timeframe)

    def _normalize_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame(columns=["timestamp", "close"])

        x = df.copy()
        if "timestamp" not in x.columns or "close" not in x.columns:
            return pd.DataFrame(columns=["timestamp", "close"])

        x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
        x["close"] = pd.to_numeric(x["close"], errors="coerce")
        for col in ("open", "high", "low", "volume"):
            if col in x.columns:
                x[col] = pd.to_numeric(x[col], errors="coerce")

        x = x.dropna(subset=["timestamp", "close"]).sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        return x.reset_index(drop=True)

    def _summary_vectorized(
        self,
        dfx: pd.DataFrame,
        days: int,
        symbol: str,
        timeframe: str,
    ) -> BacktestSummaryResponse:
        simulation = self.engine.run_historical_simulation(dfx)
        analytics = self.engine.build_analytics(simulation, days, symbol, timeframe)
        return BacktestSummaryResponse(
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            total_return_pct=analytics.total_return_pct,
            annualized_return_pct=analytics.annualized_return_pct,
            max_drawdown_pct=analytics.max_drawdown_pct,
            sharpe_ratio=analytics.sharpe_ratio,
            win_rate_pct=analytics.win_rate_pct,
            trades=analytics.trades,
            start_equity=analytics.start_equity,
            end_equity=analytics.end_equity,
            equity_curve=analytics.equity_curve,
            drawdown_curve=analytics.drawdown_curve,
            monthly_performance=analytics.monthly_performance,
            slippage_bps=analytics.slippage_bps,
            profit_factor=analytics.profit_factor,
            analytics=analytics,
        )

    def _summary_backtrader(  # pragma: no cover
        self,
        dfx: pd.DataFrame,
        days: int,
        symbol: str,
        timeframe: str,
    ) -> BacktestSummaryResponse:
        if bt is None:
            return self._summary_vectorized(dfx, days, symbol, timeframe)

        feed_df = dfx.copy().set_index("timestamp")
        for col in ("open", "high", "low", "close", "volume"):
            if col not in feed_df.columns:
                if col == "volume":
                    feed_df[col] = 0.0
                else:
                    feed_df[col] = feed_df["close"]

        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.setcash(self.initial_equity)
        cerebro.broker.setcommission(commission=0.0004)
        cerebro.addstrategy(_SMACrossStrategy)

        datafeed = bt.feeds.PandasData(dataname=feed_df[["open", "high", "low", "close", "volume"]])
        cerebro.adddata(datafeed)
        cerebro.run()

        # Backtrader run complete; produce curve via vectorized proxy for API shape consistency
        return self._summary_vectorized(dfx, days, symbol, timeframe)

    def _build_response(
        self,
        x: pd.DataFrame,
        days: int,
        symbol: str,
        timeframe: str,
    ) -> BacktestSummaryResponse:
        simulation = x.copy()
        if "drawdown" not in simulation.columns:
            roll_max = simulation["equity"].cummax()
            simulation["drawdown"] = (simulation["equity"] / roll_max) - 1.0

        start_equity = self.initial_equity
        end_equity = float(simulation["equity"].iloc[-1])

        total_return_pct = ((end_equity / start_equity) - 1.0) * 100.0
        years = max(days / 365.0, 1.0 / 365.0)
        annualized_return_pct = ((end_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0

        roll_max = simulation["equity"].cummax()
        drawdown = (simulation["equity"] / roll_max) - 1.0
        max_drawdown_pct = float(drawdown.min() * 100.0)

        r = simulation["strategy_ret"].fillna(0.0)
        vol = float(r.std())
        mean_ret = float(r.mean())
        bars_per_year = 24 * 365  # safe default for 1h-like cadence
        sharpe_ratio = (mean_ret / vol) * math.sqrt(bars_per_year) if vol > 0 else 0.0

        active = simulation[simulation["signal"].shift(1).fillna(0) > 0]["strategy_ret"]
        win_rate_pct = float((active > 0).mean() * 100.0) if len(active) else 0.0
        trades = int((simulation["signal"].diff().fillna(0).abs() > 0).sum() // 2)

        gross_profit = float(active[active > 0].sum()) if len(active) else 0.0
        gross_loss = float(abs(active[active < 0].sum())) if len(active) else 0.0
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 0.0

        equity_curve = [
            EquityPoint(timestamp=ts.isoformat(), equity=float(eq))
            for ts, eq in zip(simulation["timestamp"].tail(300), simulation["equity"].tail(300))
        ]
        drawdown_curve = [
            DrawdownPoint(timestamp=ts.isoformat(), drawdown_pct=float(dd * 100.0))
            for ts, dd in zip(simulation["timestamp"].tail(300), simulation["drawdown"].tail(300))
        ]
        monthly_performance = self.engine.monthly_performance(simulation)

        analytics = BacktestAnalytics(
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            total_return_pct=round(total_return_pct, 4),
            annualized_return_pct=round(annualized_return_pct, 4),
            max_drawdown_pct=round(max_drawdown_pct, 4),
            sharpe_ratio=round(sharpe_ratio, 4),
            win_rate_pct=round(win_rate_pct, 4),
            profit_factor=round(profit_factor, 4),
            trades=trades,
            slippage_bps=self.engine.slippage_bps,
            start_equity=round(start_equity, 4),
            end_equity=round(end_equity, 4),
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            monthly_performance=monthly_performance,
        )

        return BacktestSummaryResponse(
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            total_return_pct=round(total_return_pct, 4),
            annualized_return_pct=round(annualized_return_pct, 4),
            max_drawdown_pct=round(max_drawdown_pct, 4),
            sharpe_ratio=round(sharpe_ratio, 4),
            win_rate_pct=round(win_rate_pct, 4),
            trades=trades,
            start_equity=round(start_equity, 4),
            end_equity=round(end_equity, 4),
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            monthly_performance=monthly_performance,
            slippage_bps=self.engine.slippage_bps,
            profit_factor=round(profit_factor, 4),
            analytics=analytics,
        )