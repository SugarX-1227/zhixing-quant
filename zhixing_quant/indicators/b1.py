"""B1 indicator: KDJ oversold + 知行多空线 confirmation."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import average_ma, ema, hhv, llv, ref, sma_tdx


def add_b1_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append KDJ and B1 signal columns.

    Args:
        df: Daily OHLCV DataFrame.
        cfg: Config dictionary.

    Returns:
        Copy of df with B1-derived columns.

    Rule source:
        通达信行情指标与选股指标(1).md:
        XG: J<13 AND C>ZXDKX AND ZXDQ>ZXDKX
            AND -4% < 涨跌幅 < +4%
    """
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]

    yellow_line = average_ma(close, cfg["b1"]["yellow_ma_windows"])
    short_trend = ema(ema(close, 10), 10)

    n = int(cfg["b1"]["rsi_n"])
    rng = hhv(high, n) - llv(low, n)
    denominator = rng.replace(0, 0.001)
    rsv = ((close - llv(low, n)) / denominator * 100).clip(upper=100).fillna(50)

    k = sma_tdx(rsv, 3, 1)
    d = sma_tdx(k, 3, 1)
    j = 3 * k - 2 * d

    pct_chg = (close / ref(close, 1) - 1) * 100

    out["yellow_line"] = yellow_line
    out["short_trend"] = short_trend
    out["kdj_k"] = k
    out["kdj_d"] = d
    out["kdj_j"] = j
    out["pct_chg"] = pct_chg
    out["above_yellow"] = close > yellow_line
    out["trend_above_yellow"] = short_trend > yellow_line
    out["sig_b1"] = (
        (j < float(cfg["b1"]["j_threshold"]))
        & out["above_yellow"]
        & out["trend_above_yellow"]
        & (pct_chg > float(cfg["b1"]["chg_min"]))
        & (pct_chg < float(cfg["b1"]["chg_max"]))
    )
    return out
