"""MACD indicator with zero-axis, divergence, and false-cross detection."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import cross


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    divergence_window: int = 60,
) -> pd.DataFrame:
    """Append MACD columns.

    Args:
        df: Daily OHLCV DataFrame.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal line period.
        divergence_window: Lookback for divergence detection.

    Returns:
        Copy of df with MACD columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 3.5 节:
        - 固定参数 (12, 26, 9)
        - 三大用法：零轴多空、顶/底背离、金叉空/死叉多
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)

    out["dif"] = dif
    out["dea"] = dea
    out["macd_hist"] = macd_hist
    out["above_zero"] = dif > 0

    # Bull/bear divergence: find local lows/highs manually
    bull_div = pd.Series(False, index=close.index)
    bear_div = pd.Series(False, index=close.index)
    for i in range(divergence_window, len(close)):
        win_p = close.iloc[i - divergence_window : i + 1]
        win_d = dif.iloc[i - divergence_window : i + 1]
        p_low_idx = win_p.idxmin()
        d_low_idx = win_d.idxmin()
        p_prev_low_idx = win_p.iloc[:-1].idxmin() if len(win_p) > 1 else p_low_idx
        d_prev_low_idx = win_d.iloc[:-1].idxmin() if len(win_d) > 1 else d_low_idx
        cur_p = float(close.iloc[i])
        prev_p = float(close.loc[p_prev_low_idx]) if p_prev_low_idx in close.index else cur_p
        cur_d = float(dif.iloc[i])
        prev_d = float(dif.loc[d_prev_low_idx]) if d_prev_low_idx in dif.index else cur_d
        if cur_p < prev_p and cur_d > prev_d:
            bull_div.iloc[i] = True
        if cur_p > prev_p and cur_d < prev_d:
            bear_div.iloc[i] = True

    out["bull_divergence"] = bull_div
    out["bear_divergence"] = bear_div

    # False golden/death cross
    gold = cross(dif, dea)
    death = cross(dea, dif)
    out["false_golden_cross"] = gold & (dif.diff() < 0)
    out["false_death_cross"] = death & (dif.diff() > 0)

    return out


def _find_divergence(
    price: pd.Series,
    dif: pd.Series,
    price_low_idx: pd.Series,
    dif_low_idx: pd.Series,
    kind: str,
) -> pd.Series:
    """Detect price-indicator divergence."""
    result = pd.Series(False, index=price.index)
    for i in range(len(price)):
        if pd.isna(price_low_idx.iloc[i]) or pd.isna(dif_low_idx.iloc[i]):
            continue
        p_idx = price.index.get_loc(price_low_idx.iloc[i])
        d_idx = dif.index.get_loc(dif_low_idx.iloc[i])
        if p_idx != d_idx:
            continue
        cur_p = float(price.iloc[i])
        prev_p = float(price.iloc[p_idx]) if p_idx > 0 else cur_p
        cur_d = float(dif.iloc[i])
        prev_d = float(dif.iloc[d_idx]) if d_idx > 0 else cur_d
        if kind == "bull":
            result.iloc[i] = cur_p < prev_p and cur_d > prev_d
        else:
            result.iloc[i] = cur_p > prev_p and cur_d < prev_d
    return result
