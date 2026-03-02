from __future__ import annotations

import asyncio
import logging
import os

from app.services.trade_service import poll_order_updates

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("execution_worker")


async def run() -> None:
    interval_sec = float(os.getenv("EXECUTION_POLL_INTERVAL_SEC", "5") or "5")
    logger.info("Execution worker started (poll interval=%ss)", interval_sec)
    await poll_order_updates(interval_sec=interval_sec)


if __name__ == "__main__":
    asyncio.run(run())
