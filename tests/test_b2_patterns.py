"""三大 B2 子形态：各造一段必触发 / 差一条不触发的行情，外加未来函数截断测试。"""

import numpy as np
import pandas as pd

from zhixing_quant.indicators.b2_patterns import add_b2_pattern_indicators
from zhixing_quant.indicators.pipeline import run_steps

CFG = {"b2_patterns": {}}


def _frame(close, open_=None, high=None, low=None, vol=None):
    n = len(close)
    close = np.asarray(close, float)
    open_ = close * 0.995 if open_ is None else np.asarray(open_, float)
    high = np.maximum(close, open_) * 1.002 if high is None else np.asarray(high, float)
    low = np.minimum(close, open_) * 0.998 if low is None else np.asarray(low, float)
    vol = np.full(n, 1e6) if vol is None else np.asarray(vol, float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "vol": vol, "amount": close * vol},
                        index=pd.date_range("2023-01-02", periods=n, freq="B"))


# ---------------------------------------------------------------------------
# 平行重炮
# ---------------------------------------------------------------------------

def _parallel(middle_is_yin=True):
    base = [10.0] * 150
    close = base + [10.5, 10.4, 10.35, 10.9]           # 长阳 / 阴 / 阴 / 长阳突破
    open_ = base + [10.0, 10.5, 10.4, 10.36]
    if not middle_is_yin:
        open_[151] = 10.3                              # 中间那根改成阳线
    vol = [1e6] * 150 + [3e6, 1.5e6, 1.2e6, 3.5e6]
    return _frame(close, open_=open_, vol=vol)


def test_parallel_heavy_cannon_fires_on_second_long_yang():
    out = add_b2_pattern_indicators(_parallel(), CFG)
    hits = out.index[out["sig_b2_parallel"]]
    assert list(hits) == [out.index[153]]


def test_parallel_requires_yin_bars_in_between():
    out = add_b2_pattern_indicators(_parallel(middle_is_yin=False), CFG)
    assert not out["sig_b2_parallel"].any()


# ---------------------------------------------------------------------------
# 灾后重建
# ---------------------------------------------------------------------------

def _rebuild(vol_mult=3.0):
    close = [10.0] * 120 + [11.0] * 10 + [10.8, 10.5, 11.1]
    open_ = list(close)
    open_[-1] = 10.5                                    # 最后一根阳线
    low = [c * 0.998 for c in close]
    low[-2] = 9.9                                       # 回踩打到黄线
    high = [max(o, c) * 1.002 for o, c in zip(open_, close)]
    vol = [1e6] * 133
    vol[122] = 9e6                                      # 重成交区，收盘 11.0
    vol[130] = 1.2e6                                    # 击穿日
    vol[131] = 0.8e6                                    # 缩量回踩
    vol[132] = 0.8e6 * vol_mult                         # 倍量长阳
    return _frame(close, open_=open_, high=high, low=low, vol=vol)


def test_rebuild_fires_after_break_and_pullback():
    out = add_b2_pattern_indicators(_rebuild(), CFG)
    assert bool(out["sig_b2_rebuild"].iloc[-1])
    assert out["sig_b2_rebuild"].sum() == 1


def test_rebuild_requires_double_volume():
    out = add_b2_pattern_indicators(_rebuild(vol_mult=1.5), CFG)
    assert not out["sig_b2_rebuild"].any()


# ---------------------------------------------------------------------------
# 跃跃欲试
# ---------------------------------------------------------------------------

def _eager(tests=2):
    close = [10.0] * 140 + [10.05, 10.1, 10.08, 10.12, 10.1, 10.15, 10.12,
                            10.18, 10.15, 10.2, 10.6]
    open_ = list(close)
    for i in range(140, 150):                           # 平台内 7 阳 3 阴
        open_[i] = close[i] - 0.03 if i % 3 else close[i] + 0.03
    open_[150] = 10.2
    high = [max(o, c) * 1.001 for o, c in zip(open_, close)]
    for i in (143, 146, 149)[:tests]:                   # 冲高回落 = 试盘
        high[i] = max(open_[i], close[i]) + 0.2
    vol = [1e6] * 151
    for i in range(140, 150):
        vol[i] = 1.5e6 if close[i] > open_[i] else 0.8e6
    vol[150] = 2e6
    return _frame(close, open_=open_, high=high, vol=vol)


def test_eager_fires_on_breakout_after_tests():
    out = add_b2_pattern_indicators(_eager(tests=2), CFG)
    assert bool(out["sig_b2_eager"].iloc[-1])


def test_eager_requires_enough_tests():
    out = add_b2_pattern_indicators(_eager(tests=1), CFG)
    assert not bool(out["sig_b2_eager"].iloc[-1])


# ---------------------------------------------------------------------------
# 未来函数 / 接线
# ---------------------------------------------------------------------------

def test_no_pattern_reads_the_future():
    """截掉后半段，前半段的四列信号必须逐位不变。"""
    rng = np.random.default_rng(7)
    n = 600
    close = np.clip(10 + np.cumsum(rng.normal(0, 0.25, n)), 1, None)
    open_ = np.concatenate([[10.0], close[:-1]]) * (1 + rng.normal(0, 0.01, n))
    vol = rng.lognormal(14, 0.6, n)
    df = _frame(close, open_=open_, vol=vol)
    loose = {"b2_patterns": {"long_pct": 1.0, "platform_range": 0.15, "min_tests": 1,
                             "rebuild_vol_mult": 1.2, "parallel_pct_tol": 5.0}}
    cols = ["sig_b2_parallel", "sig_b2_rebuild", "sig_b2_eager", "sig_b2_patterns"]
    full = add_b2_pattern_indicators(df, loose)[cols]
    cut = add_b2_pattern_indicators(df.iloc[:400], loose)[cols]
    assert full.iloc[:400].any().any()                 # 确实有信号，不是空对空
    pd.testing.assert_frame_equal(full.iloc[:400], cut)


def test_pipeline_step_is_registered():
    res = run_steps(_parallel(), CFG, ["b2_patterns"])
    assert res.ok
    assert "sig_b2_patterns" in res.df.columns
