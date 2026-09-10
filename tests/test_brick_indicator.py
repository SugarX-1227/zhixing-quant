import pandas as pd

from zhixing_quant.indicators.brick import add_brick_indicators, brick_signal_columns
from zhixing_quant.indicators.tdx import sma_tdx


CFG = {
    "execution": {"abandon_gap_up": 0.07},
    "brick": {
        "n1": 4,
        "n2": 6,
        "min_brick_height": 4.0,
        "min_brick_growth": 1.5,
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


def test_green_to_strong_red_matches_appendix_b5():
    """附录 B.5：强度确认 = 砖高 > 4 AND 砖高 > 昨日砖高 × 1.5。"""
    # 10 → 4（绿）→ 8（红）。8 > 4 且 8 > 4×1.5=6，两道门槛都过。
    result = brick_signal_columns(pd.Series([10.0, 4.0, 8.0]))

    assert bool(result["brick_yesterday_green"].iloc[-1])
    assert bool(result["brick_today_red"].iloc[-1])
    assert bool(result["brick_strong_abs"].iloc[-1])
    assert bool(result["brick_strong_rel"].iloc[-1])
    assert bool(result["brick_height_ok"].iloc[-1])


def test_micro_red_is_rejected_by_absolute_floor():
    """防微红盘：砖高从 0.1 涨到 0.5，涨了 5 倍，但绝对高度不到 4。

    这正是原实现（今日涨幅 >= 昨日跌幅 × 2/3）会放行、
    而作者在公式注释里明说要挡掉的情况。
    """
    result = brick_signal_columns(pd.Series([2.0, 0.1, 0.5]))

    assert bool(result["brick_today_red"].iloc[-1])
    assert bool(result["brick_strong_rel"].iloc[-1]), "0.5 > 0.1×1.5，相对强度是够的"
    assert not bool(result["brick_strong_abs"].iloc[-1]), "但砖高 0.5 < 4"
    assert not bool(result["brick_height_ok"].iloc[-1])


def test_slow_climb_is_rejected_by_relative_floor():
    """缓慢爬升：砖高 20 → 21，绝对够高但增长不到 1.5 倍。"""
    result = brick_signal_columns(pd.Series([25.0, 20.0, 21.0]))

    assert bool(result["brick_strong_abs"].iloc[-1])
    assert not bool(result["brick_strong_rel"].iloc[-1])
    assert not bool(result["brick_height_ok"].iloc[-1])


def test_old_two_thirds_misreading_is_gone():
    """回归锁：确保不会有人把 2/3 那套改回来。

    砖高 30 → 10（跌 20）→ 22（涨 12）。
    旧规则：涨幅 12 >= 跌幅 20 × 2/3 = 13.33 → False，本来就不过。
    换个数：30 → 10 → 25，涨幅 15 >= 13.33 → 旧规则 True。
    新规则：25 > 10×1.5 = 15 → 也 True。两者在这里一致，所以用
    下面这组把它们分开：20 → 18 → 26，涨幅 8 >= 跌幅 2×2/3 → 旧规则 True，
    而 26 > 18×1.5 = 27 不成立 → 新规则 False。
    """
    result = brick_signal_columns(pd.Series([20.0, 18.0, 26.0]))

    assert bool(result["brick_today_red"].iloc[-1])
    assert not bool(result["brick_height_ok"].iloc[-1]), \
        "26 没到 18×1.5=27，按附录 B.5 不该放行"
