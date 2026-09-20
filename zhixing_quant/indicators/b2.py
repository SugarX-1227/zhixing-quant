"""B2 indicator: KDJ oversold history + volume breakout."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.b1 import add_b1_indicators
from zhixing_quant.indicators.tdx import (attach_zhixing_lines, exist, hhv, llv, ref, sma_tdx)


def add_b2_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append KDJ and B2 signal columns.

    Args:
        df: Daily OHLCV DataFrame.
        cfg: Config dictionary.

    Returns:
        Copy of df with B2-derived columns.

    Rule source:
        通达信行情指标与选股指标(1).md:
        COND1: 近N日J<13
        COND2: 涨幅 > 3.95%
        COND3: VOL > 前日VOL
        COND4: J < 55
        XG: COND1 AND COND2 AND COND3 AND COND4

        规划书 4.3.1 对 B2 的定性描述比上面的通达信公式更严：
        「B1 低点的确认阳线，紧跟 B1 出现，非凭空大阳线，用于确认 B1 有效性；
        钩明显上翘，放量中长阳」。对应下面三条可选收紧条件，
        配置项全部 0 / false = 关闭，此时 sig_b2 与通达信原公式逐位相同。
    """
    out = df.copy()
    close = out["close"]
    open_ = out["open"]
    high = out["high"]
    low = out["low"]
    vol = out["vol"]

    attach_zhixing_lines(out, cfg)

    n = int(cfg["b2"]["rsi_n"])
    rng = hhv(high, n) - llv(low, n)
    denominator = rng.replace(0, 0.001)
    rsv = ((close - llv(low, n)) / denominator * 100).clip(upper=100).fillna(50)

    k = sma_tdx(rsv, 3, 1)
    d = sma_tdx(k, 3, 1)
    j = 3 * k - 2 * d

    pct_chg = (close / ref(close, 1) - 1) * 100
    vol_last = ref(vol, 1)

    exist_window = int(cfg["b2"]["exist_window"])
    j_was_oversold = exist(j < float(cfg["b2"]["j_oversold"]), exist_window)

    out["kdj_k"] = k
    out["kdj_d"] = d
    out["kdj_j"] = j
    out["pct_chg"] = pct_chg
    out["vol_last"] = vol_last
    out["j_was_oversold"] = j_was_oversold
    out["volume_up"] = vol > vol_last
    sig = (
        j_was_oversold
        & (pct_chg > float(cfg["b2"]["chg_min"]))
        & out["volume_up"]
        & (j < float(cfg["b2"]["j_threshold"]))
    )

    b2cfg = cfg["b2"]

    # 「紧跟 B1 出现」：当日之前 N 个交易日内出现过 B1。先 shift(1) 再 rolling，
    # 把当日排除在外——B1 要求涨跌幅 < +4%、B2 要求 > 3.95%，同日几乎互斥，
    # 但口径上「确认阳线」本就该在 B1 之后。sig_b1 直接取 b1 模块的结果，
    # 不在这里重抄一遍公式（黄线曾因两处各算一份而跑在两条不同的线上）。
    within = int(b2cfg.get("b1_within_days", 0) or 0)
    if within > 0:
        sig_b1 = add_b1_indicators(df, cfg)["sig_b1"].fillna(False).astype(bool)
        out["b1_recent"] = (
            sig_b1.shift(1).rolling(within, min_periods=1).max().fillna(0).astype(bool)
        )
        sig &= out["b1_recent"]

    # 规划书里还有一条「钩明显上翘」。没有实现，因为它是哑开关：
    # 原公式已要求当日涨幅 > 3.95%，涨这么多 J 几乎必然上行——
    # 实测 14380 个 B2 信号里 99.5% 自动满足 j > ref(j,1)，
    # 加上它之后回测结果与基线逐位相同。要重新实现得换更严的口径。

    # 「放量中长阳、无长上下影」：实体占当日全幅的比例。用有符号实体，
    # 阴线自然为负被滤掉。一字板全幅为 0 → NaN → 判False，与「买不进」一致。
    body_min = float(b2cfg.get("body_ratio_min", 0.0) or 0.0)
    if body_min > 0:
        day_range = (high - low).replace(0, float("nan"))
        out["body_ratio"] = (close - open_) / day_range
        sig &= out["body_ratio"] >= body_min

    out["sig_b2"] = sig
    return out
