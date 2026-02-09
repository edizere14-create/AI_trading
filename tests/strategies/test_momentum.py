import pandas as pd
import pytest

from app.strategies.momentum import MomentumStrategy

def test_generate_signal_buy() -> None:
    df = pd.DataFrame({"rsi": [25]})
    strat = MomentumStrategy()
    assert strat.generate_signal(df) == {"signal": "buy", "confidence": 0.8}

def test_generate_signal_sell() -> None:
    df = pd.DataFrame({"rsi": [75]})
    strat = MomentumStrategy()
    assert strat.generate_signal(df) == {"signal": "sell", "confidence": 0.8}

def test_generate_signal_hold() -> None:
    df = pd.DataFrame({"rsi": [50]})
    strat = MomentumStrategy()
    assert strat.generate_signal(df) == {"signal": "hold", "confidence": 0.5}

def test_missing_rsi_column_raises() -> None:
    df = pd.DataFrame({"close": [100]})
    strat = MomentumStrategy()
    with pytest.raises(ValueError):
        strat.generate_signal(df)

def test_empty_rsi_raises() -> None:
    df = pd.DataFrame({"rsi": []})
    strat = MomentumStrategy()
    with pytest.raises(ValueError):
        strat.generate_signal(df)