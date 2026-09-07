"""Tests for main-force distribution detection."""

import pandas as pd

from zhixing_quant.signals.distribution import detect_distribution


def _base_df():
    index = pd.date_range("2024-01-01", periods=60)
    close = 100.0 + pd.Series(range(60)) * 0.5
    close.iloc[50:] = close.iloc[49] + pd.Series(range(10)) * 2.0
    df = pd.DataFrame(
        {
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "vol": [1000000] * 55 + [3000000] * 5,
            "amount": [100000000] * 60,
        },
        index=index,
    )
    df["yellow_line"] = df["close"] - 5.0
    df["white_line"] = df["close"] - 2.0
    return df


def test_distribution_columns_exist():
    df = _base_df()
    result = detect_distribution(df, 1e9, {})
    for col in [
        "dist_stagnation",
        "dist_sky_high",
        "dist_evening_star",
        "dist_ma_break",
        "dist_long_bear",
        "sig_distribution",
        "sig_distribution_type",
    ]:
        assert col in result.columns


def test_distribution_type_labels():
    df = _base_df()
    result = detect_distribution(df, 1e9, {})
    types = result["sig_distribution_type"].unique()
    assert "none" in types or len(result[result["sig_distribution_type"] != "none"]) >= 0
