from pydantic import BaseModel, Field
from datetime import datetime
import pandas as pd

class IndicatorRequest(BaseModel):
    symbol: str
    timeframe: str = Field(default="1h", description="Timeframe (1m, 5m, 1h, 4h, 1d)")
    period: int = Field(default=14, ge=2, description="Period for calculation")

class RSIResponse(BaseModel):
    symbol: str
    timeframe: str
    rsi: float = Field(ge=0, le=100)
    period: int
    timestamp: datetime

class MACDResponse(BaseModel):
    symbol: str
    timeframe: str
    macd: float
    signal: float
    histogram: float
    period: int
    timestamp: datetime

def calc_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    if not {"high", "low", "close"}.issubset(df.columns):
        return pd.Series([float("nan")] * len(df), index=df.index)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1
    ).max(axis=1)
    return tr.rolling(length, min_periods=1).mean()

def calc_liquidity(df: pd.DataFrame, vol_len: int = 20) -> pd.Series:
    if "volume" not in df.columns:
        return pd.Series([float("nan")] * len(df), index=df.index)
    v = df["volume"].astype(float)
    return v.rolling(vol_len, min_periods=1).mean()

def calc_spread(df: pd.DataFrame) -> pd.Series:
    if {"bid", "ask"}.issubset(df.columns):
        return (df["ask"].astype(float) - df["bid"].astype(float)).abs()
    if "spread" in df.columns:
        return df["spread"].astype(float).abs()
    return pd.Series([float("nan")] * len(df), index=df.index)

def regime_mask(
    df: pd.DataFrame,
    atr_len: int = 14,
    atr_min: float = 0.0,
    vol_len: int = 20,
    vol_min: float = 0.0,
    spread_max: float = float("inf"),
) -> pd.Series:
    atr = calc_atr(df, atr_len)
    liq = calc_liquidity(df, vol_len)
    spr = calc_spread(df)

    ok_atr = (atr >= atr_min) if atr_min > 0 else True
    ok_vol = (liq >= vol_min) if vol_min > 0 else True
    ok_spr = (spr <= spread_max) if spread_max < float("inf") else True

    # Broadcast booleans properly
    mask = pd.Series(True, index=df.index)
    if isinstance(ok_atr, pd.Series):
        mask &= ok_atr.fillna(False)
    if isinstance(ok_vol, pd.Series):
        mask &= ok_vol.fillna(False)
    if isinstance(ok_spr, pd.Series):
        mask &= ok_spr.fillna(False)
    return mask

def filter_signals_by_regime(
    df: pd.DataFrame,
    signals: list,
    atr_len: int = 14,
    atr_min: float = 0.0,
    vol_len: int = 20,
    vol_min: float = 0.0,
    spread_max: float = float("inf"),
):
    if not signals:
        return []
    mask = regime_mask(df, atr_len, atr_min, vol_len, vol_min, spread_max)
    out = []
    for s in signals:
        idx = int(s.get("index", -1))
        if 0 <= idx < len(df) and bool(mask.iloc[idx]):
            out.append(s)
    return out