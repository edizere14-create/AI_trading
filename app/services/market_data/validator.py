from __future__ import annotations

from app.models.market import Candle, Tick


def validate_candle(c: Candle) -> list[str]:
    errors: list[str] = []
    if c.high < c.low:
        errors.append("high < low")
    if c.open < 0 or c.high < 0 or c.low < 0 or c.close < 0:
        errors.append("negative price")
    if not (c.low <= c.open <= c.high):
        errors.append("open outside [low, high]")
    if not (c.low <= c.close <= c.high):
        errors.append("close outside [low, high]")
    return errors


def validate_tick(t: Tick) -> list[str]:
    errors: list[str] = []
    if t.bid is not None and t.ask is not None and t.bid > t.ask:
        errors.append("bid > ask")
    return errors