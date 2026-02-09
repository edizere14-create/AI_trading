from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_timeframe(tf_str: str) -> str:
    # Placeholder: return normalized timeframe string
    return tf_str.strip().lower()