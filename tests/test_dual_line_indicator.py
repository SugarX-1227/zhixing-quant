"""知行双线测试。

这个文件以前只断言"列存在"，所以白线写成 ema(C,10)、黄线写成 ma(C,20)
的时候它全绿。现在改成逐值比对附录 B.1 的原始公式。
"""

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.indicators.dual_line import add_dual_line
from zhixing_quant.indicators.tdx import ema, ma


def _series(values) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2023-01-02", periods=len(values)),
                     dtype=float)


def _frame(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": close, "high": close * 1.02, "low": close * 0.98, "close": close,
         "vol": [1_000_000.0] * len(close), "amount": close * 1e6},
        index=close.index,
    )


# ---------------------------------------------------------------------------
# 公式本身
# ---------------------------------------------------------------------------

def test_white_line_is_double_smoothed_ema10():
    """附录 B.1：知行短期趋势线 = EMA(EMA(C,10),10)。

    不是 EMA(C,10)，也不是 MA(C,10)。文档 3.1 解释过为什么要二次平滑：
    单条 EMA10 噪声大、假突破多，白线用于判定"波段是否结束"，
    宁可晚一点确认也不要被洗出去。
    """
    rng = np.random.default_rng(0)
    close = _series(20 * np.cumprod(1 + rng.normal(0.001, 0.02, 300)))
    out = add_dual_line(_frame(close))

    assert np.allclose(out["white_line"], ema(ema(close, 10), 10), equal_nan=True)
    assert not np.allclose(out["white_line"], ema(close, 10), equal_nan=True), \
        "白线退化成了单条 EMA10"
    assert not np.allclose(out["white_line"], ma(close, 10), equal_nan=True), \
        "白线退化成了 MA10"


def test_yellow_line_is_average_of_four_mas():
    """附录 B.1：知行多空线 = (MA14 + MA28 + MA57 + MA114) / 4。"""
    rng = np.random.default_rng(1)
    close = _series(30 * np.cumprod(1 + rng.normal(0.0005, 0.018, 400)))
    out = add_dual_line(_frame(close))

    expected = sum(ma(close, w) for w in (14, 28, 57, 114)) / 4
    valid = out["lines_valid"]
    assert np.allclose(out.loc[valid, "yellow_line"], expected[valid])
    assert not np.allclose(out.loc[valid, "yellow_line"], ma(close, 20)[valid]), \
        "黄线退化成了 MA20，跌破黄线的判定会完全跑偏"


def test_yellow_line_contains_a_114_day_anchor():
    """黄线必须对 114 根之前的价格有记忆，否则它不是中长期锚。

    前 200 根在 10 元，后 30 根跳到 20 元。此时 MA14/MA28 已完全跟上（=20），
    MA57=15.26、MA114=12.63 仍拖在后面，黄线 16.97。若黄线只含短周期，
    它应该已经等于 20。
    """
    close = _series([10.0] * 200 + [20.0] * 30)
    out = add_dual_line(_frame(close))

    yellow_last = float(out["yellow_line"].iloc[-1])
    assert yellow_last < 18.0, f"黄线 {yellow_last:.2f} 太贴近现价，长周期分量丢了"
    assert float(ma(close, 20).iloc[-1]) == pytest.approx(20.0), "对照：MA20 早就跟上去了"


# ---------------------------------------------------------------------------
# 预热保护
# ---------------------------------------------------------------------------

def test_yellow_line_is_nan_before_114_bars():
    """ma() 用 min_periods=1，K线不够时会拿残缺窗口算出一个像模像样的数。

    文档 3.1 提醒过次新股/刚复牌的票黄线失真。这里必须是 NaN，
    并且 lines_valid 要能让调用方区分"没触发"和"没法判"。
    """
    close = _series([10.0 + i * 0.1 for i in range(150)])
    out = add_dual_line(_frame(close))

    assert out["yellow_line"].iloc[:113].isna().all()
    assert out["yellow_line"].iloc[113:].notna().all()
    assert not out["lines_valid"].iloc[:113].any()
    assert out["lines_valid"].iloc[113:].all()


# ---------------------------------------------------------------------------
# 派生判据
# ---------------------------------------------------------------------------

def test_regime_strong_when_white_above_yellow():
    close = _series([100.0] * 150 + [100 + i * 0.8 for i in range(120)])
    out = add_dual_line(_frame(close))
    assert bool(out["regime_strong"].iloc[-1])
    assert not bool(out["regime_weak"].iloc[-1])


def test_golden_cross_on_a_downtrend_to_uptrend_reversal():
    """空头转多头：白线从黄线下方上穿，应只有金叉、没有死叉。"""
    close = _series([100 - i * 0.3 for i in range(160)]
                    + [52 + i * 0.8 for i in range(150)])
    out = add_dual_line(_frame(close))

    after = out.loc[out["lines_valid"]]
    assert int(after["golden_cross"].sum()) == 1
    assert int(after["death_cross"].sum()) == 0


def test_double_ema_lags_ma14_on_the_first_bars_of_a_breakout():
    """锁住一个反直觉但正确的行为，防止有人当成 bug 改回单条 EMA。

    白线是**双重** EMA10，首根响应约 (2/11)² ≈ 3.3%；而黄线里的 MA14
    首根响应是 1/14 ≈ 7.1%。所以横盘平台刚起涨的头几根，黄线会比白线
    涨得快，先出一次死叉，随后白线加速才补上金叉。

    这是二次平滑的固有代价，文档 3.1 说得很清楚：白线宁可晚一点确认，
    也不要被洗出去。看到"平台起涨先死叉"不要去改公式。
    """
    close = _series([100.0] * 150 + [100 + i * 0.8 for i in range(150)])
    out = add_dual_line(_frame(close))

    after = out.loc[out["lines_valid"]]
    assert int(after["death_cross"].sum()) == 1, "起涨首根黄线短暂反超，属预期"
    assert int(after["golden_cross"].sum()) == 1

    death_at = after.index[after["death_cross"]][0]
    golden_at = after.index[after["golden_cross"]][0]
    assert death_at < golden_at, "死叉必须发生在金叉之前，否则是真的走坏了"


def test_all_expected_columns_present():
    close = _series([100.0 + i * 0.05 for i in range(200)])
    out = add_dual_line(_frame(close))
    for col in ("white_line", "yellow_line", "lines_valid", "regime_strong",
                "regime_weak", "golden_cross", "death_cross",
                "above_white", "above_yellow"):
        assert col in out.columns, col
