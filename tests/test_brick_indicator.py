import pandas as pd

from zhixing_quant.indicators.brick import add_brick_indicators, brick_signal_columns
from zhixing_quant.indicators.tdx import sma_tdx


CFG = {
    "execution": {"abandon_gap_up": 0.07},
    "brick": {
        "n1": 4,
        "n2": 6,
        "yellow_ma_windows": [14, 28, 57, 114],
        "min_height_ratio": 2 / 3,
    },
}


def test_sma_tdx_uses_recursive_tongdaxin_formula():
    source = pd.Series([10.0, 20.0, 30.0])

    result = sma_tdx(source, 4, 1)

    assert result.round(6).tolist() == [10.0, 12.5, 16.875]


def test_brick_signal_requires_yesterday_green_today_red_height_and_yellow():
    index = pd.date_range("2024-01-01", periods=8)
    df = pd.DataFrame(
        {
            "open": [10, 9, 8, 7, 8, 9, 10, 11],
            "high": [10.2, 9.2, 8.2, 7.2, 8.5, 9.5, 10.5, 12.5],
            "low": [9.8, 8.8, 7.8, 6.8, 7.5, 8.5, 9.5, 10.5],
            "close": [10, 9, 8, 7, 8, 9, 10, 12],
            "vol": [100] * 8,
            "amount": [100000000] * 8,
        },
        index=index,
    )

    result = add_brick_indicators(df, CFG)

    assert "yellow_line" in result.columns
    assert "brick_value" in result.columns
    assert "sig_brick" in result.columns
    assert result["abandon_gap_up_price"].iloc[-1] == result["close"].iloc[-1] * 1.07


def test_brick_signal_columns_match_tongdaxin_height_rule():
    values = pd.Series([10.0, 4.0, 8.0])

    result = brick_signal_columns(values, 2 / 3)

    assert bool(result["brick_yesterday_green"].iloc[-1])
    assert bool(result["brick_today_red"].iloc[-1])
    assert result["brick_green_height"].iloc[-1] == 6.0
    assert result["brick_red_height"].iloc[-1] == 4.0
    assert bool(result["brick_height_ok"].iloc[-1])
