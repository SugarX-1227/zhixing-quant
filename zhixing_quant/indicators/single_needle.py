"""Single needle under 30 indicator (proxy version without chip distribution)."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import llv, ref


def add_single_needle(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append single needle indicator columns.

    Args:
        df: Daily OHLCV DataFrame.
        cfg: Config dictionary.

    Returns:
        Copy of df with single needle columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 4 个选股指标:
        - 长期 > 85 AND 短期 < 30
        - 机构线/散户线代理：由于没有 Level-2 数据，用价格位置代理
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")

    n1 = int(cfg.get("single_needle", {}).get("n1", 10))
    n2 = int(cfg.get("single_needle", {}).get("n2", 20))

    short_term = 100 * (close - llv(low, n1)) / (hhv(close, n1) - llv(low, n1) + 0.001)
    medium_term = 100 * (close - llv(low, 10)) / (hhv(close, 10) - llv(low, 10) + 0.001)
    mid_long_term = 100 * (close - llv(low, 20)) / (hhv(close, 20) - llv(low, 20) + 0.001)
    long_term = 100 * (close - llv(low, n2)) / (hhv(close, n2) - llv(low, n2) + 0.001)

    out["single_needle_short"] = short_term
    out["single_needle_medium"] = medium_term
    out["single_needle_mid_long"] = mid_long_term
    out["single_needle_long"] = long_term
    out["sig_single_needle_30"] = (long_term > 85) & (short_term < 30)

    return out


def hhv(series: pd.Series, n: int) -> pd.Series:
    """Rolling highest value."""
    return pd.to_numeric(series, errors="coerce").rolling(n, min_periods=1).max()
