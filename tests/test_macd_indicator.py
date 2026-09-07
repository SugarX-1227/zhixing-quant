import pandas as pd

from zhixing_quant.indicators.macd import add_macd


def test_macd_basic_columns():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100 + i for i in range(30)],
            "high": [102 + i for i in range(30)],
            "low": [98 + i for i in range(30)],
            "close": [101 + i for i in range(30)],
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_macd(df)

    assert "dif" in result.columns
    assert "dea" in result.columns
    assert "macd_hist" in result.columns
    assert "above_zero" in result.columns
    assert "bull_divergence" in result.columns
    assert "bear_divergence" in result.columns
    assert "false_golden_cross" in result.columns
    assert "false_death_cross" in result.columns
    assert len(result) == 30


def test_macd_above_zero_tracks_dif():
    index = pd.date_range("2024-01-01", periods=30)
    close = pd.Series([100 + i * 2 for i in range(30)], index=index)
    df = pd.DataFrame(
        {"open": close, "high": close + 2, "low": close - 2, "close": close, "vol": [1000000] * 30, "amount": [100000000] * 30},
        index=index,
    )
    result = add_macd(df)
    assert result["above_zero"].equals(result["dif"] > 0)


def test_macd_false_cross_not_simple_cross():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100.0] * 30,
            "high": [102.0] * 30,
            "low": [98.0] * 30,
            "close": [100.0] * 30,
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_macd(df)
    # Flat market: no cross events
    assert not result["false_golden_cross"].any()
    assert not result["false_death_cross"].any()
