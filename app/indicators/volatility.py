def calculate_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if not (highs and lows and closes):
        return 0.0
    if len(highs) != len(lows) or len(lows) != len(closes):
        raise ValueError("highs, lows, closes must have same length")
    if period <= 0:
        raise ValueError("period must be positive")

    trs = []
    prev_close = closes[0]
    for h, l, c in zip(highs, lows, closes):
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c

    if len(trs) < period:
        return sum(trs) / len(trs)
    window = trs[-period:]
    return sum(window) / period