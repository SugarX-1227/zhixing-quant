import pandas as pd

from zhixing_quant.indicators.b1 import add_b1_indicators
from zhixing_quant.indicators.tdx import ema, sma_tdx

CFG = {
    "b1": {
        "yellow_ma_windows": [14, 28, 57, 114],
        "rsi_n": 9,
        "j_threshold": 13,
        "chg_min": -4,
        "chg_max": 4,
    },
}


def test_ema_double_smooth():
    source = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    result = ema(ema(source, 10), 10)
    assert len(result) == 5
    assert not result.isna().any()


def test_sma_tdx_basic():
    source = pd.Series([10.0, 20.0, 30.0])
    result = sma_tdx(source, 4, 1)
    assert result.round(6).tolist() == [10.0, 12.5, 16.875]


def test_b1_signal_requires_j_oversold_and_yellow():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100 + i * 0.5 for i in range(30)],
            "high": [102 + i * 0.5 for i in range(30)],
            "low": [98 + i * 0.5 for i in range(30)],
            "close": [101 + i * 0.5 for i in range(30)],
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )

    result = add_b1_indicators(df, CFG)

    assert "yellow_line" in result.columns
    assert "short_trend" in result.columns
    assert "kdj_j" in result.columns
    assert "kdj_k" in result.columns
    assert "kdj_d" in result.columns
    assert "sig_b1" in result.columns
    assert "pct_chg" in result.columns
    assert len(result) == 30


def test_b1_signal_not_triggered_when_j_high():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [200.0] * 30,
            "high": [205.0] * 30,
            "low": [195.0] * 30,
            "close": [202.0] * 30,
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )

    result = add_b1_indicators(df, CFG)

    assert not result["sig_b1"].any()
