"""Momentum strategy worker with execution."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.data_service import DataService
from app.core.execution_engine import ExecutionEngine
from app.core.risk_manager import RiskManager
from app.strategies.momentum_strategy import MomentumStrategy

logger = logging.getLogger(__name__)


class MomentumWorker:
    """Background worker for momentum trading strategy."""

    def __init__(
        self,
        strategy: MomentumStrategy,
        data_service: DataService,
        execution_engine: ExecutionEngine,
        risk_manager: RiskManager,
        interval: int = 300,  # 5 minutes
    ):
        """Initialize momentum worker.
        
        Args:
            strategy: Momentum strategy instance
            data_service: Data service for market data
            execution_engine: Execution engine for placing orders
            risk_manager: Risk manager for position/risk checks
            interval: Interval between iterations in seconds
        """
        self.strategy = strategy
        self.data_service = data_service
        self.execution_engine = execution_engine
        self.risk_manager = risk_manager
        self.interval = interval
        
        self.is_running = False
        self.task: asyncio.Task | None = None
        self.symbol: str = ""
        
        # Metrics
        self.signal_count = 0
        self.execution_count = 0
        self.last_signal: dict[str, Any] | None = None
        self.signal_history: list[dict[str, Any]] = []

    async def start(self, symbol: str) -> dict[str, Any]:
        """Start the momentum worker.
        
        Args:
            symbol: Trading symbol (e.g., 'PI_XBTUSD')
            
        Returns:
            Status dictionary
        """
        if self.is_running:
            return {"status": "already_running", "symbol": self.symbol}
        
        self.symbol = symbol
        self.is_running = True
        self.task = asyncio.create_task(self._run())
        
        logger.info(f"✅ Momentum worker started for {symbol}")
        return {"status": "started", "symbol": symbol}

    async def stop(self) -> dict[str, Any]:
        """Stop the momentum worker.
        
        Returns:
            Status dictionary
        """
        if not self.is_running:
            return {"status": "not_running"}
        
        self.is_running = False
        
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info(f"⏹️ Momentum worker stopped for {self.symbol}")
        return {"status": "stopped"}

    async def _run(self):
        """Main worker loop."""
        logger.info(f"🔄 Momentum worker loop started for {self.symbol}")
        
        while self.is_running:
            try:
                await self._run_iteration()
            except asyncio.CancelledError:
                logger.info("Worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                await asyncio.sleep(self.interval)

    async def _run_iteration(self):
        """Run one iteration of the momentum strategy."""
        try:
            # Generate signal
            signal = await self._generate_signal()
            
            if signal:
                self.last_signal = signal
                self.signal_count += 1
                self.signal_history.append(signal)
                
                logger.info(f"✅ Signal generated: {signal}")
                
                # Execute the signal
                try:
                    execution_result = await self.execution_engine.execute_signal(signal)
                    
                    if execution_result:
                        self.execution_count += 1
                        logger.info(f"✅ Trade executed: {execution_result}")
                    else:
                        logger.warning(f"⚠️ Signal not executed (filtered by risk manager)")
                        
                except Exception as e:
                    logger.error(f"❌ Execution failed: {e}", exc_info=True)
            else:
                logger.debug(f"No signal generated for {self.symbol}")
            
            # Wait for next iteration
            await asyncio.sleep(self.interval)
            
        except Exception as e:
            logger.error(f"Error in iteration: {e}", exc_info=True)
            await asyncio.sleep(self.interval)

    async def _generate_signal(self) -> dict[str, Any] | None:
        """Generate trading signal.
        
        Returns:
            Signal dictionary or None if no signal
        """
        try:
            # Fetch market data
            df = await self.data_service.get_ohlcv(
                symbol=self.symbol,
                timeframe="1m",
                limit=100
            )
            
            if df is None or df.empty:
                logger.warning(f"No data available for {self.symbol}")
                return None
            
            # Generate signal using strategy
            signal = self.strategy.generate_signal(df, self.symbol)
            
            if signal:
                # Add timestamp
                signal["timestamp"] = datetime.now(timezone.utc).isoformat()
                
            return signal
            
        except Exception as e:
            logger.error(f"Error generating signal: {e}", exc_info=True)
            return None

    def get_status(self) -> dict[str, Any]:
        """Get worker status.
        
        Returns:
            Status dictionary with metrics
        """
        return {
            "is_running": self.is_running,
            "symbol": self.symbol,
            "signal_count": self.signal_count,
            "execution_count": self.execution_count,
            "last_signal": self.last_signal,
            "risk": self.risk_manager.get_risk_metrics(),
        }

    def get_history(self, limit: int = 50) -> dict[str, Any]:
        """Get signal history.
        
        Args:
            limit: Maximum number of signals to return
            
        Returns:
            History dictionary
        """
        signals = self.signal_history[-limit:] if self.signal_history else []
        return {
            "symbol": self.symbol,
            "count": len(signals),
            "signals": signals,
        }