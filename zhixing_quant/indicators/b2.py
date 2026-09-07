"""B2 indicator: KDJ oversold history + volume breakout."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import average_ma, exist, hhv, llv, ref, sma_tdx


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
    """
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    vol = out["vol"]

    out["yellow_line"] = average_ma(close, cfg["b2"]["yellow_ma_windows"])

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
    out["sig_b2"] = (
        j_was_oversold
        & (pct_chg > float(cfg["b2"]["chg_min"]))
        & out["volume_up"]
        & (j < float(cfg["b2"]["j_threshold"]))
    )
    return out
