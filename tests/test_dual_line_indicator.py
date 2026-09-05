import pandas as pd

from zhixing_quant.indicators.dual_line import add_dual_line


def test_dual_line_columns():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100.0] * 30,
            "high": [102.0] * 30,
            "low": [98.0] * 30,
            "close": [101.0] * 30,
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_dual_line(df)

    assert "white_line" in result.columns
    assert "yellow_line" in result.columns
    assert "regime_strong" in result.columns
    assert "regime_weak" in result.columns
    assert "golden_cross" in result.columns
    assert "death_cross" in result.columns
    assert "above_yellow" in result.columns
    assert "above_white" in result.columns


def test_regime_strong_when_white_above_yellow():
    index = pd.date_range("2024-01-01", periods=30)
    close = pd.Series([100 + i * 0.5 for i in range(30)], index=index)
    df = pd.DataFrame(
        {"open": close, "high": close + 2, "low": close - 2, "close": close, "vol": [1000000] * 30, "amount": [100000000] * 30},
        index=index,
    )
    result = add_dual_line(df)
    assert result["regime_strong"].iloc[-1]


def test_golden_cross_detected():
    index = pd.date_range("2024-01-01", periods=30)
    close = pd.Series([100.0] * 15 + [110.0] * 15, index=index)
    df = pd.DataFrame(
        {"open": close, "high": close + 2, "low": close - 2, "close": close, "vol": [1000000] * 30, "amount": [100000000] * 30},
        index=index,
    )
    result = add_dual_line(df)
    assert result["golden_cross"].any()
    assert not result["death_cross"].any()
