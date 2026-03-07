"""
Multi-symbol scanner worker.

Runs gate logic across multiple symbols every N minutes and promotes
the highest-confidence signal to the momentum worker for execution.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

SCAN_SYMBOLS = [
    s.strip()
    for s in os.getenv("SCANNER_SYMBOLS", "PF_XBTUSD,PF_ETHUSD,PF_SOLUSD").split(",")
    if s.strip()
]

SCANNER_ENABLED = os.getenv("SCANNER_ENABLED", "false").lower() in {"1", "true", "yes"}

SCAN_INTERVAL_SEC = float(os.getenv("SCANNER_INTERVAL_SEC", "120"))


class ScannerWorker:
    """
    Runs gate logic across multiple symbols every N minutes.
    Promotes the highest-confidence signal to the momentum worker.
    """

    def __init__(self, momentum_worker: Any) -> None:
        self.worker = momentum_worker
        self.running = False
        self._task: asyncio.Task | None = None
        self.last_scan_result: dict[str, Any] | None = None
        self.scan_count: int = 0
        logger.info(
            "ScannerWorker init | enabled=%s symbols=%s interval=%.0fs",
            SCANNER_ENABLED,
            SCAN_SYMBOLS,
            SCAN_INTERVAL_SEC,
        )

    async def scan(self) -> dict[str, Any] | None:
        """
        Evaluate gate logic for each symbol/side combination.
        Returns the strongest passing signal, or None if nothing qualifies.
        """
        best: dict[str, Any] | None = None

        for symbol in SCAN_SYMBOLS:
            try:
                candles = await self.worker._load_ohlcv(symbol, "1m", 50)
                if candles is None or candles.empty:
                    logger.debug("Scanner: no candles for %s", symbol)
                    continue

                for side in ("buy", "sell"):
                    allowed, reason, snapshot = self.worker._entry_gate_allows_execution(
                        candles, side
                    )
                    if allowed:
                        score = float(snapshot.get("confidence_pct", 0))
                        if best is None or score > best["score"]:
                            best = {
                                "symbol": symbol,
                                "side": side,
                                "score": score,
                                "reason": reason,
                                "snapshot": snapshot,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
            except Exception as exc:
                logger.warning("Scanner: error scanning %s: %s", symbol, exc)

        self.scan_count += 1
        self.last_scan_result = best
        if best:
            logger.info(
                "Scanner picked %s %s (confidence=%.2f%%)",
                best["symbol"],
                best["side"],
                best["score"],
            )
        else:
            logger.debug("Scanner: no qualifying signals across %d symbols", len(SCAN_SYMBOLS))

        return best

    async def _loop(self) -> None:
        """Background loop: scan → promote → sleep."""
        while self.running:
            try:
                result = await self.scan()
                if result:
                    # Switch the momentum worker to the best symbol if different
                    current_symbol = getattr(self.worker, "symbol", None)
                    if result["symbol"] != current_symbol:
                        logger.info(
                            "Scanner promoting symbol %s → %s",
                            current_symbol,
                            result["symbol"],
                        )
                        self.worker.symbol = result["symbol"]
            except Exception as exc:
                logger.error("Scanner loop error: %s", exc)

            await asyncio.sleep(SCAN_INTERVAL_SEC)

    def start(self) -> None:
        """Start the scanner background loop."""
        if not SCANNER_ENABLED:
            logger.info("Scanner disabled (SCANNER_ENABLED != true)")
            return
        if self.running:
            return
        self.running = True
        self._task = asyncio.ensure_future(self._loop())
        logger.info("ScannerWorker started")

    def stop(self) -> None:
        """Stop the scanner."""
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("ScannerWorker stopped")

    def status(self) -> dict[str, Any]:
        """Return scanner status for API/dashboard."""
        return {
            "enabled": SCANNER_ENABLED,
            "running": self.running,
            "symbols": SCAN_SYMBOLS,
            "scan_count": self.scan_count,
            "last_result": self.last_scan_result,
            "interval_sec": SCAN_INTERVAL_SEC,
        }
