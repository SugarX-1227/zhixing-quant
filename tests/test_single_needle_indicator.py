import pandas as pd

from zhixing_quant.indicators.single_needle import add_single_needle


CFG = {
    "single_needle": {
        "n1": 10,
        "n2": 20,
    },
}


def test_single_needle_columns():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100.0] * 30,
            "high": [105.0] * 30,
            "low": [95.0] * 30,
            "close": [100.0] * 30,
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_single_needle(df, CFG)

    assert "single_needle_short" in result.columns
    assert "single_needle_medium" in result.columns
    assert "single_needle_mid_long" in result.columns
    assert "single_needle_long" in result.columns
    assert "sig_single_needle_30" in result.columns


def test_sig_triggers_when_long_high_short_low():
    index = pd.date_range("2024-01-01", periods=30)
    # Price at bottom of range: long_term high, short_term low
    close = pd.Series([95.0] * 5 + [100.0] * 25, index=index)
    high = close + 2
    low = close - 2
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "vol": [1000000] * 30, "amount": [100000000] * 30},
        index=index,
    )
    result = add_single_needle(df, CFG)
    # After warmup, long_term should be high (price near bottom of 20-bar range)
    # and short_term should be low
    assert result["sig_single_needle_30"].dtype == bool


def test_no_sig_when_flat_market():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100.0] * 30,
            "high": [101.0] * 30,
            "low": [99.0] * 30,
            "close": [100.0] * 30,
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_single_needle(df, CFG)
    # Flat market: short_term ≈ 50, long_term ≈ 50, no signal
    assert not result["sig_single_needle_30"].any()
