import pandas as pd
import pytest

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


def test_green_to_strong_red_matches_the_tdx_formula():
    """通达信原文：红柱高度 >= 绿柱高度 × 2/3。

    30 → 10 → 25：绿柱高度 = 30-10 = 20，红柱高度 = 25-10 = 15。
    15 >= 20 × 2/3 = 13.33 → 达标。
    """
    result = brick_signal_columns(pd.Series([30.0, 10.0, 25.0]))

    assert bool(result["brick_yesterday_green"].iloc[-1])
    assert bool(result["brick_today_red"].iloc[-1])
    assert result["brick_green_height"].iloc[-1] == pytest.approx(20.0)
    assert result["brick_red_height"].iloc[-1] == pytest.approx(15.0)
    assert bool(result["brick_height_ok"].iloc[-1])


def test_weak_red_below_two_thirds_is_rejected():
    """30 → 10 → 20：红柱高度 10 < 20 × 2/3 = 13.33，不达标。"""
    result = brick_signal_columns(pd.Series([30.0, 10.0, 20.0]))

    assert bool(result["brick_today_red"].iloc[-1])
    assert not bool(result["brick_height_ok"].iloc[-1])


def test_yesterday_green_requires_strict_decline():
    """通达信是 REF(砖型图,1) < REF(砖型图,2)，严格小于。

    旧实现用 ~ref(today_red,1)，等价于 <=，会把「昨日持平」也算成绿柱。
    """
    flat = brick_signal_columns(pd.Series([20.0, 20.0, 25.0]))
    assert not bool(flat["brick_yesterday_green"].iloc[-1]), \
        "昨日持平不是绿柱，通达信用的是严格小于"

    down = brick_signal_columns(pd.Series([20.0, 19.0, 25.0]))
    assert bool(down["brick_yesterday_green"].iloc[-1])


def test_the_x15_misreading_stays_gone():
    """回归锁：旧实现是「砖高>4 且 砖高>昨日砖高×1.5」，比的是水平值不是增量。

    实测 50 只 × 2.8 年，旧实现只有 1 次信号，通达信公式有 1553 次；
    规划书 6.8 的验收标准是 200-400 次/年。砖型图本该是信号最多的一套。
    """
    # 砖高 100 → 80 → 95：绿柱高度 20，红柱高度 15 >= 13.33 → 通达信放行。
    # 旧实现要求 95 > 80×1.5 = 120 → 拒绝。这组能把两者分开。
    result = brick_signal_columns(pd.Series([100.0, 80.0, 95.0]))
    assert bool(result["brick_height_ok"].iloc[-1]), \
        "又被改回成比水平值了——砖型图会重新退化成哑战法"


def test_optional_extra_gates_are_off_by_default():
    """min_brick_height / min_brick_growth 不属于通达信公式，默认必须不生效。"""
    default = brick_signal_columns(pd.Series([100.0, 80.0, 95.0]))
    assert bool(default["brick_height_ok"].iloc[-1])

    gated = brick_signal_columns(pd.Series([100.0, 80.0, 95.0]),
                                 min_brick_growth=1.5)
    assert not bool(gated["brick_height_ok"].iloc[-1]), "附加门槛没起作用"


def test_shipped_config_uses_the_tdx_ratio():
    from zhixing_quant.config import load_config

    brick = load_config()["brick"]
    assert brick["min_height_ratio"] == pytest.approx(2 / 3, abs=1e-3)
    assert float(brick["min_brick_height"]) == 0.0, "附加门槛应默认关闭"
    assert float(brick["min_brick_growth"]) == 0.0


def test_brick_signal_fires_at_a_usable_rate_on_trending_data():
    """砖型图在实盘里是信号最多的战法。构造一段有涨有跌的行情，
    整段一次都不触发就说明判据又被卡死了。"""
    import numpy as np

    rng = np.random.default_rng(3)
    n = 400
    close = np.clip(30 + np.cumsum(rng.normal(0.02, 0.8, n)), 5, None)
    prev = np.concatenate([[30.0], close[:-1]])
    df = pd.DataFrame(
        {
            "open": prev,
            "high": np.maximum(close, prev) + abs(rng.normal(0, .3, n)),
            "low": np.minimum(close, prev) - abs(rng.normal(0, .3, n)),
            "close": close,
            "vol": [1e6] * n,
            "amount": [1e8] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )
    hits = int(add_brick_indicators(df, load_shipped_cfg())["sig_brick"].sum())
    assert hits > 5, f"400 根 K 线只触发 {hits} 次，判据又被卡死了"


def load_shipped_cfg() -> dict:
    from zhixing_quant.config import load_config

    return load_config()
