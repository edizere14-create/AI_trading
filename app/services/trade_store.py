from __future__ import annotations

from threading import Lock
from typing import Any


class TradeStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._trades: list[dict[str, Any]] = []

    def add(self, trade: dict[str, Any]) -> None:
        with self._lock:
            self._trades.append(trade)

    def add_trade(self, trade: dict[str, Any]) -> None:
        self.add(trade)

    def save_trade(self, trade: dict[str, Any]) -> None:
        self.add(trade)

    def record_trade(self, trade: dict[str, Any]) -> None:
        self.add(trade)

    def append(self, trade: dict[str, Any]) -> None:
        self.add(trade)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return self._trades[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._trades.clear()


trade_store = TradeStore()