from datetime import datetime
from typing import List

import pandas as pd

from app.schemas.strategy import StrategyRunResult, Trade
from app.services.data_service import get_historical_candles
from app.indicators.momentum import calculate_rsi, calculate_macd
from app.utils.ai_models import TradingAIModels

async def run_strategy(
    strategy_code: str,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    capital: float = 10_000,
    ai_models: TradingAIModels | None = None,
) -> StrategyRunResult:
    if ai_models is None:
        ai_models = TradingAIModels()

    candles = await get_historical_candles(symbol, timeframe, start, end, limit=500)
    if not candles:
        return StrategyRunResult(
            total_return=0.0,
            max_drawdown=0.0,
            sharpe=None,
            trades=[],
        )

    df = pd.DataFrame([c.dict() for c in candles])
    df["rsi"] = calculate_rsi(df["close"].values)
    df["macd"], _ = calculate_macd(df["close"].values)

    equity = capital
    position = 0.0
    trades: List[Trade] = []

    for i in range(20, len(df)):
        close_now = float(df["close"].iloc[i])
        features = [
            float(df["rsi"].iloc[i]),
            float(df["macd"].iloc[i]),
            close_now / float(df["close"].iloc[i - 1]) - 1,
            float(df["volume"].iloc[i]) / float(df["volume"].iloc[i - 1] or 1.0),
        ]
        signal = ai_models.predict_momentum_signal(features)

        if signal["signal"] == 1 and position <= 0:  # buy
            size = (equity / close_now) * 0.95
            trades.append(
                Trade(
                    timestamp=df["timestamp"].iloc[i],
                    side="buy",
                    price=close_now,
                    size=size,
                )
            )
            position += size
            equity -= size * close_now
        elif signal["signal"] == 2 and position > 0:  # sell
            trades.append(
                Trade(
                    timestamp=df["timestamp"].iloc[i],
                    side="sell",
                    price=close_now,
                    size=position,
                )
            )
            equity += position * close_now
            position = 0.0

    final_equity = equity + position * float(df["close"].iloc[-1])
    total_return = (final_equity - capital) / capital

    return StrategyRunResult(
        total_return=float(total_return),
        max_drawdown=0.0,
        sharpe=None,
        trades=trades,
    )
