"""Tests for sell signal series S1/S2/S3."""

import pandas as pd

from zhixing_quant.signals.sell_s import detect_s_series


def _base_df():
    index = pd.date_range("2024-01-01", periods=30)
    close = 100.0 + (pd.Series(range(30)) * 0.3)
    close.iloc[-5:] = close.iloc[-6] - pd.Series(range(5)) * 2.0
    df = pd.DataFrame(
        {
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "vol": [1000000] * 25 + [3000000, 2800000, 3200000, 3100000, 2900000],
            "amount": [100000000] * 30,
        },
        index=index,
    )
    df["yellow_line"] = df["close"] - 5.0
    df["white_line"] = df["close"] - 2.0
    df["dif"] = 0.0
    df["dea"] = 0.0
    df["false_death_cross"] = False
    return df


def test_sell_s_columns_exist():
    df = _base_df()
    result = detect_s_series(df, {})
    for col in ["sig_s1", "sig_s2", "sig_s3", "sig_s_stop"]:
        assert col in result.columns


def test_s1_triggers_on_yellow_break_with_volume():
    index = pd.date_range("2024-01-01", periods=20)
    close = [100.0 + i * 0.5 for i in range(15)] + [107.5 - 4.0, 107.5 - 5.5, 107.5 - 7.0, 107.5 - 8.5, 107.5 - 10.0]
    df = pd.DataFrame(
        {
            "open": [c - 1.0 for c in close],
            "high": [c + 2.0 for c in close],
            "low": [c - 2.0 for c in close],
            "close": close,
            "vol": [1000000] * 15 + [1000000, 1000000, 1000000, 1000000, 4000000],
            "amount": [100000000] * 20,
        },
        index=index,
    )
    df["yellow_line"] = 106.0  # independent of close
    df["white_line"] = 104.0
    df["dif"] = 0.0
    df["dea"] = 0.0
    df["false_death_cross"] = False
    result = detect_s_series(df, {})
    assert bool(result["sig_s1"].iloc[-1])


def test_s3_triggers_on_big_bear_with_double_vol():
    index = pd.date_range("2024-01-01", periods=10)
    df = pd.DataFrame(
        {
            "open": [110.0] * 10,
            "high": [112.0] * 10,
            "low": [88.0] * 10,
            "close": [88.0] * 10,
            "vol": [1000000] * 9 + [3000000],
            "amount": [100000000] * 10,
        },
        index=index,
    )
    df["yellow_line"] = 99.0
    df["white_line"] = 100.0
    df["dif"] = 0.0
    df["dea"] = 0.0
    df["false_death_cross"] = False
    result = detect_s_series(df, {})
    assert bool(result["sig_s3"].iloc[-1])


def test_s2_triggers_on_white_break_plus_death_cross():
    index = pd.date_range("2024-01-01", periods=15)
    df = pd.DataFrame(
        {
            "open": [100.0 + i * 0.5 for i in range(15)],
            "high": [102.0 + i * 0.5 for i in range(15)],
            "low": [98.0 + i * 0.5 for i in range(15)],
            "close": [101.0 + i * 0.5 for i in range(15)],
            "vol": [1000000] * 15,
            "amount": [100000000] * 15,
        },
        index=index,
    )
    df["yellow_line"] = 108.0
    df["white_line"] = 106.0
    df["dif"] = 0.0
    df["dea"] = 0.0
    df["false_death_cross"] = False
    result = detect_s_series(df, {})
    assert not bool(result["sig_s2"].iloc[-1])


def test_sig_s_stop_equals_low():
    df = _base_df()
    result = detect_s_series(df, {})
    assert result["sig_s_stop"].equals(result["low"])
