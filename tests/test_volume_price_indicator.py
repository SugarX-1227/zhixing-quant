import pandas as pd

from zhixing_quant.indicators.volume_price import add_volume_price


def test_volume_price_columns_exist():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100.0] * 30,
            "high": [102.0] * 30,
            "low": [98.0] * 30,
            "close": [101.0] * 30,
            "vol": [1000000 + i * 50000 for i in range(30)],
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_volume_price(df)

    expected = [
        "vol_ratio_prev", "is_double_vol", "is_sky_vol", "is_low_vol",
        "is_floor_vol", "is_flat_vol", "pullback_low_vol",
    ]
    for col in expected:
        assert col in result.columns


def test_double_vol_triggers():
    index = pd.date_range("2024-01-01", periods=5)
    df = pd.DataFrame(
        {
            "open": [100.0] * 5,
            "high": [102.0] * 5,
            "low": [98.0] * 5,
            "close": [101.0] * 5,
            "vol": [1000000, 2000000, 3000000, 4000000, 5000000],
            "amount": [100000000] * 5,
        },
        index=index,
    )
    result = add_volume_price(df)
    assert bool(result["is_double_vol"].iloc[1])
    assert not bool(result["is_double_vol"].iloc[0])


def test_sky_vol_requires_two_consecutive_double_vol():
    index = pd.date_range("2024-01-01", periods=5)
    df = pd.DataFrame(
        {
            "open": [100.0] * 5,
            "high": [102.0] * 5,
            "low": [98.0] * 5,
            "close": [101.0] * 5,
            "vol": [1000000, 2000000, 4000000, 8000000, 16000000],
            "amount": [100000000] * 5,
        },
        index=index,
    )
    result = add_volume_price(df)
    assert not bool(result["is_sky_vol"].iloc[1])
    assert bool(result["is_sky_vol"].iloc[2])


def test_pullback_low_vol_requires_price_above_ma5():
    index = pd.date_range("2024-01-01", periods=10)
    # Rising price + decreasing volume triggers is_low_vol at some bars
    close = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]
    vols = [2000000, 1500000, 1000000, 800000, 600000, 500000, 400000, 300000, 250000, 200000]
    df = pd.DataFrame(
        {
            "open": [c - 1 for c in close],
            "high": [c + 2 for c in close],
            "low": [c - 2 for c in close],
            "close": close,
            "vol": vols,
            "amount": [100000000] * 10,
        },
        index=index,
    )
    result = add_volume_price(df)
    # At index 7: is_low_vol=True and close > close_ma5
    assert bool(result["pullback_low_vol"].iloc[7])
