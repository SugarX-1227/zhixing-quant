import pandas as pd

from zhixing_quant.indicators.b2 import add_b2_indicators
from zhixing_quant.indicators.tdx import exist

CFG = {
    "b2": {
        "yellow_ma_windows": [14, 28, 57, 114],
        "rsi_n": 9,
        "exist_window": 3,
        "j_oversold": 13,
        "chg_min": 3.95,
        "j_threshold": 55,
    },
}


def test_exist_true_when_condition_within_window():
    cond = pd.Series([False, True, False, False])
    result = exist(cond, 3)
    assert bool(result.iloc[3])


def test_exist_false_when_condition_outside_window():
    cond = pd.Series([True, False, False, False])
    result = exist(cond, 3)
    assert not bool(result.iloc[3])


def test_b2_requires_oversold_history_volume_and_chg():
    index = pd.date_range("2024-01-01", periods=15)
    df = pd.DataFrame(
        {
            "open": [100.0] * 15,
            "high": [105.0] * 15,
            "low": [95.0] * 15,
            "close": [101.0 + i * 0.3 for i in range(15)],
            "vol": [1000000 + i * 100000 for i in range(15)],
            "amount": [100000000] * 15,
        },
        index=index,
    )

    result = add_b2_indicators(df, CFG)

    assert "kdj_j" in result.columns
    assert "kdj_k" in result.columns
    assert "kdj_d" in result.columns
    assert "j_was_oversold" in result.columns
    assert "volume_up" in result.columns
    assert "sig_b2" in result.columns
    assert len(result) == 15


def test_b2_not_triggered_when_no_oversold_history():
    index = pd.date_range("2024-01-01", periods=15)
    df = pd.DataFrame(
        {
            "open": [200.0] * 15,
            "high": [205.0] * 15,
            "low": [195.0] * 15,
            "close": [202.0] * 15,
            "vol": [1000000] * 15,
            "amount": [100000000] * 15,
        },
        index=index,
    )

    result = add_b2_indicators(df, CFG)

    assert not result["sig_b2"].any()
