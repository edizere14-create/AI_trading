from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
import logging
from typing import List, Literal

import pandas as pd

from app.schemas.strategy import (
    StrategyRunRequest,
    StrategyRunResult,
    Trade,
    StrategySignalResponse,
    TimeframeSignal,
)

logger = logging.getLogger(__name__)

async def run_backtest(
    db: AsyncSession,
    request: StrategyRunRequest
) -> StrategyRunResult:
    """Run a backtest with the given strategy."""
    try:
        logger.info("Running backtest for %s", request.symbol)
        
        # TODO: Implement actual backtest logic
        # Mock data for now
        mock_trades: List[Trade] = [
            Trade(
                timestamp=datetime.now(timezone.utc),
                side="buy",
                price=50000.0,
                size=0.1
            ),
            Trade(
                timestamp=datetime.now(timezone.utc),
                side="sell",
                price=51000.0,
                size=0.1
            )
        ]
        
        return StrategyRunResult(
            total_return=0.05,  # 5% return
            max_drawdown=0.02,  # 2% drawdown
            sharpe=1.5,
            trades=mock_trades
        )
    except Exception as exc:
        logger.error("Error running backtest: %s", exc)
        return StrategyRunResult(
            total_return=0.0,
            max_drawdown=0.0,
            sharpe=None,
            trades=[]
        )

async def validate_strategy_code(code: str) -> bool:
    """Validate strategy code for security."""
    try:
        # Basic validation - prevent dangerous imports/functions
        dangerous_keywords = [
            "import os",
            "import sys",
            "exec(",
            "eval(",
            "__import__",
            "open(",
            "file("
        ]
        
        for keyword in dangerous_keywords:
            if keyword in code:
                logger.warning("Dangerous keyword detected: %s", keyword)
                return False
        
        return True
    except Exception as exc:
        logger.error("Error validating strategy code: %s", exc)
        return False

"""Strategy service for managing trading strategies."""
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class StrategyService:
    """Service for managing and executing strategies."""
    
    def __init__(self) -> None:
        self.strategies: Dict[str, Any] = {}
    
    async def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Get strategy by ID."""
        return self.strategies.get(strategy_id)
    
    async def list_strategies(self) -> List[Dict[str, Any]]:
        """List all strategies."""
        return list(self.strategies.values())
    
    async def create_strategy(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new strategy."""
        strategy_id = f"strategy_{len(self.strategies)}"
        strategy = {
            "id": strategy_id,
            "name": name,
            "config": config,
            "status": "active",
        }
        self.strategies[strategy_id] = strategy
        logger.info(f"Strategy created: {strategy_id}")
        return strategy
    
    async def delete_strategy(self, strategy_id: str) -> bool:
        """Delete a strategy."""
        if strategy_id in self.strategies:
            del self.strategies[strategy_id]
            logger.info(f"Strategy deleted: {strategy_id}")
            return True
        return False


class StrategyEngine:
    MODE_MULTIPLIER = {
        "conservative": 0.6,
        "balanced": 1.0,
        "aggressive": 1.4,
    }

    def __init__(self, base_size_pct: float = 0.02) -> None:
        self.base_size_pct = float(base_size_pct)

    def _normalize_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame(columns=["timestamp", "close"])

        x = df.copy()
        if "close" not in x.columns:
            return pd.DataFrame(columns=["timestamp", "close"])

        if "timestamp" in x.columns:
            x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
        else:
            x["timestamp"] = pd.RangeIndex(start=0, stop=len(x), step=1)

        x["close"] = pd.to_numeric(x["close"], errors="coerce")
        x = x.dropna(subset=["close"]).sort_values("timestamp")
        return x.reset_index(drop=True)

    def _timeframe_signal(self, timeframe: str, df: pd.DataFrame) -> TimeframeSignal:
        x = self._normalize_frame(df)
        if len(x) < 30:
            return TimeframeSignal(
                timeframe=timeframe,
                momentum=0.0,
                trend_strength=0.0,
                volatility=0.0,
                score=0.0,
            )

        close = x["close"]
        ret = close.pct_change().fillna(0.0)
        momentum = float((close.iloc[-1] / close.iloc[-10] - 1.0) * 100.0) if len(close) >= 10 else 0.0

        fast = close.rolling(10).mean().iloc[-1]
        slow = close.rolling(30).mean().iloc[-1]
        if pd.isna(fast) or pd.isna(slow) or slow == 0:
            trend_strength = 0.0
        else:
            trend_strength = float((fast / slow - 1.0) * 100.0)

        volatility = float(ret.tail(30).std() * (252 ** 0.5) * 100.0)
        raw_score = (momentum * 0.45) + (trend_strength * 0.45) - (volatility * 0.10)
        score = float(max(-100.0, min(100.0, raw_score)))

        return TimeframeSignal(
            timeframe=timeframe,
            momentum=round(momentum, 6),
            trend_strength=round(trend_strength, 6),
            volatility=round(volatility, 6),
            score=round(score, 6),
        )

    def analyze_multi_timeframe(self, frames: dict[str, pd.DataFrame]) -> list[TimeframeSignal]:
        ordered: list[TimeframeSignal] = []
        for timeframe, df in frames.items():
            ordered.append(self._timeframe_signal(timeframe, df))
        return ordered

    def _aggressiveness(self, mode: str, volatility: float) -> float:
        mode_key = mode if mode in self.MODE_MULTIPLIER else "balanced"
        volatility_penalty = max(0.5, 1.0 - min(volatility, 80.0) / 160.0)
        return float(max(0.2, min(2.0, self.MODE_MULTIPLIER[mode_key] * volatility_penalty)))

    def generate_signal(
        self,
        symbol: str,
        frames: dict[str, pd.DataFrame],
        mode: str = "balanced",
    ) -> StrategySignalResponse:
        multi = self.analyze_multi_timeframe(frames)
        if not multi:
            return StrategySignalResponse(
                symbol=symbol,
                mode="balanced",
                action="hold",
                confidence=0.0,
                aggressiveness=1.0,
                suggested_size_pct=0.0,
                multi_timeframe=[],
                metadata={"reason": "no_data"},
            )

        aggregate_score = float(sum(item.score for item in multi) / len(multi))
        avg_vol = float(sum(item.volatility for item in multi) / len(multi))
        aggressiveness = self._aggressiveness(mode, avg_vol)

        threshold = 8.0
        action: Literal["buy", "sell", "hold"]
        if aggregate_score > threshold:
            action = "buy"
        elif aggregate_score < -threshold:
            action = "sell"
        else:
            action = "hold"

        confidence = float(min(1.0, abs(aggregate_score) / 100.0))
        suggested_size_pct = float(self.base_size_pct * aggressiveness * confidence)

        normalized_mode: Literal["conservative", "balanced", "aggressive"]
        if mode == "conservative":
            normalized_mode = "conservative"
        elif mode == "aggressive":
            normalized_mode = "aggressive"
        else:
            normalized_mode = "balanced"
        return StrategySignalResponse(
            symbol=symbol,
            mode=normalized_mode,
            action=action,
            confidence=round(confidence, 6),
            aggressiveness=round(aggressiveness, 6),
            suggested_size_pct=round(max(0.0, suggested_size_pct), 6),
            multi_timeframe=multi,
            metadata={
                "aggregate_score": round(aggregate_score, 6),
                "average_volatility": round(avg_vol, 6),
            },
        )
