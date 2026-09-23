"""三大 B2 子形态：平行重炮 / 灾后重建 / 跃跃欲试。

出处：知行量化系统开发规划.docx 4.3.3 与 6.3。规划书只给了一行「机械化定义」，
原始手册（5.2 章）不在仓库里，所以下面每一处歧义都按字面最直接的读法定口径，
阈值全部进 `settings.yaml` 的 `b2_patterns` 段。

在此之前 `strategies/b2_strategy.py` 里的三种「模式」与规划书毫无关系：它只是把
通用 sig_b2 的命中按量能切成三份打标签，本质仍是通用 B2。这里是按原文重新实现，
每个形态都是独立的信号，**不以通用 sig_b2 为前提**。

平行重炮（规划书：两根平行放量长阳，涨幅 ≥4% 且接近，中间 1-3 根缩量阴，
第二根放量突破前一根高点）
    长阳 = 阳线 且 涨幅 ≥ long_pct 且 量 > 前日量；
    「接近」= 两根涨幅相差 ≤ parallel_pct_tol 个百分点；
    中间每一根都是阴线，且量都小于第一根长阳；
    当日（第二根）收盘 > 第一根最高价。

灾后重建（规划书：前 N=10 内出现 cover_trapped + 缩量打到黄线后，一根倍量长阳
反包前一根低点；cover_trapped = 穿过近 60 日最大成交量价位，需配合筹码模块）
    没有筹码模块，「击穿重成交区」近似为：收盘从上方跌破「近 heavy_lookback 日
    最大量那天的收盘价」；
    「缩量打到黄线」= 最低价 ≤ 黄线 且 量 < 前日量，且不早于击穿日；
    两者都在当日之前 rebuild_window 个交易日内；
    当日：阳线、涨幅 ≥ long_pct、量 ≥ rebuild_vol_mult × 前日量、
    收盘 > 前一根最高价（原文「反包前一根低点」读不通，按反包前一根 K 线处理）。

跃跃欲试（规划书：横盘 ≥10 日，振幅 < 5%，期间「红肥绿瘦」（红 K 数量 > 绿 K，
红 K 总量 > 绿 K 总量），第 3 次试盘后突破）
    平台 = 当日之前 platform_days 日，收盘价区间 (最高收盘-最低收盘)/最低收盘
    < platform_range；
    红肥绿瘦 = 平台内阳线根数 > 阴线根数 且 阳线总量 > 阴线总量；
    试盘 = 冲高回落：上影线 ≥ 实体 且 上影线 > 0，平台内至少 min_tests 次；
    当日：阳线 且 收盘 > 平台期最高价——即第 min_tests+1 次冲击成功。

全部只用当日及之前的数据（shift 只向后看），见 tests/test_b2_patterns.py 的
截断测试。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from zhixing_quant.indicators.tdx import attach_zhixing_lines, hhv, llv, ref

DEFAULTS = {
    "long_pct": 4.0,
    "parallel_pct_tol": 3.0,
    "rebuild_window": 10,
    "heavy_lookback": 60,
    "rebuild_vol_mult": 2.0,
    "platform_days": 10,
    "platform_range": 0.05,
    "min_tests": 2,
}


def _bars_since(cond: pd.Series) -> pd.Series:
    """通达信 BARSLAST：距上一次 cond 为真过了几根，当根为真记 0，从未为真为 NaN。"""
    idx = pd.Series(np.arange(len(cond)), index=cond.index, dtype=float)
    last = idx.where(cond.fillna(False).astype(bool)).ffill()
    return idx - last


def _heavy_price(close: pd.Series, vol: pd.Series, lookback: int) -> pd.Series:
    """当日之前 lookback 根里成交量最大那天的收盘价（不含当日）。"""
    v = vol.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) > lookback:
        win = np.lib.stride_tricks.sliding_window_view(v[:-1], lookback)
        arg = np.nanargmax(np.where(np.isnan(win), -np.inf, win), axis=1)
        pos = np.arange(len(win)) + arg                 # 在原序列里的下标
        out[lookback:] = c[pos]
    return pd.Series(out, index=close.index)


def add_b2_pattern_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """追加 sig_b2_parallel / sig_b2_rebuild / sig_b2_eager / sig_b2_patterns 四列。"""
    p = {**DEFAULTS, **((cfg or {}).get("b2_patterns", {}) or {})}
    out = df.copy()
    attach_zhixing_lines(out, cfg)
    o, c, h, lo, v = (out[k].astype(float) for k in ("open", "close", "high", "low", "vol"))
    pct = (c / ref(c, 1) - 1) * 100
    yang, yin = c > o, c < o
    long_yang = yang & (pct >= float(p["long_pct"])) & (v > ref(v, 1))

    # ---- 平行重炮：第一根在 T-k（k=2..4），中间 k-1 根缩量阴 ----
    parallel = pd.Series(False, index=out.index)
    for k in (2, 3, 4):
        first_vol = ref(v, k)
        between = pd.Series(True, index=out.index)
        for j in range(1, k):
            between &= yin.shift(j, fill_value=False) & (ref(v, j) < first_vol)
        parallel |= (long_yang.shift(k, fill_value=False) & between
                     & long_yang & (c > ref(h, k))
                     & ((pct - ref(pct, k)).abs() <= float(p["parallel_pct_tol"])))

    # ---- 灾后重建 ----
    heavy = _heavy_price(c, v, int(p["heavy_lookback"]))
    broke = (c < heavy) & (ref(c, 1) >= heavy)
    pullback = (lo <= out["yellow_line"]) & (v < ref(v, 1))
    since_break = ref(_bars_since(broke), 1)          # 截至昨日
    since_pull = ref(_bars_since(pullback), 1)
    w = int(p["rebuild_window"])
    rebuild = ((since_break <= w - 1) & (since_pull <= since_break)
               & yang & (pct >= float(p["long_pct"]))
               & (v >= float(p["rebuild_vol_mult"]) * ref(v, 1))
               & (c > ref(h, 1)))

    # ---- 跃跃欲试 ----
    n = int(p["platform_days"])
    c_hi, c_lo = ref(hhv(c, n), 1), ref(llv(c, n), 1)
    platform = (c_hi - c_lo) / c_lo < float(p["platform_range"])
    red_fat = ((ref(yang.astype(int).rolling(n).sum(), 1)
                > ref(yin.astype(int).rolling(n).sum(), 1))
               & (ref((v * yang).rolling(n).sum(), 1) > ref((v * yin).rolling(n).sum(), 1)))
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    test = (upper >= (c - o).abs()) & (upper > 0)
    tests = ref(test.astype(int).rolling(n).sum(), 1)
    eager = (platform & red_fat & (tests >= int(p["min_tests"]))
             & yang & (c > ref(hhv(h, n), 1)))

    out["sig_b2_parallel"] = parallel.fillna(False).astype(bool)
    out["sig_b2_rebuild"] = rebuild.fillna(False).astype(bool)
    out["sig_b2_eager"] = eager.fillna(False).astype(bool)
    out["sig_b2_patterns"] = out[["sig_b2_parallel", "sig_b2_rebuild",
                                  "sig_b2_eager"]].any(axis=1)
    return out

