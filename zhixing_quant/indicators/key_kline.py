"""Key K-line pattern detection with position context."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import (atr as atr_of, hhv, llv, ma, ref,
                                          zhixing_white, zhixing_yellow)


def detect_key_k(df: pd.DataFrame, atr_window: int = 14, lookback: int = 60) -> pd.DataFrame:
    """Detect key K-line patterns with position context.

    Args:
        df: Daily OHLCV DataFrame.
        atr_window: ATR calculation window.
        lookback: Lookback for position checks.

    Returns:
        Copy of df with key K columns and is_key_k composite.

    Rule source:
        通达信行情指标与选股指标(1).md 第 3.2 节:
        - 10 pattern types (V-reversal, emergency brake, flat thunder, etc.)
        - 4 position flags (near yellow/white line, platform breakout, trapped)
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    pre_close = pd.to_numeric(ref(close, 1), errors="coerce")

    # ATR：走 tdx.atr，避免出场规则层和这里各算一份（数学完全相同）
    atr = atr_of(high, low, close, atr_window)

    # Candlestick body and shadows
    body = (close - open_).abs()
    body_pct = body / open_.replace(0, float("nan"))
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low
    amplitude = (high - low) / pre_close.replace(0, float("nan"))

    # Past N-bar aggregates
    pct_chg_5 = (close / ref(close, 5) - 1) * 100
    pct_chg_10 = (close / ref(close, 10) - 1) * 100
    pct_chg_5_body = ((close - ref(close, 5)) / ref(close, 5) * 100)

    # 10 pattern types
    out["k_v_reversal"] = (
        (pct_chg_5 <= -8)
        & (close > open_)
        & (body_pct >= 0.04)
        & (close > hhv(high, 5).shift(1).rolling(5, min_periods=1).median())
    )

    out["k_emergency_brake"] = (
        (pct_chg_5 <= -6)
        & (body < atr * 0.3)
        & (close >= pre_close)
    )

    platform_high = hhv(high, 10).shift(1)
    platform_low = llv(low, 10).shift(1)
    platform_range = platform_high / platform_low.replace(0, float("nan"))
    out["k_flat_thunder"] = (
        (amplitude < 0.03)
        .rolling(10, min_periods=10).sum()
        .shift(1)
        .fillna(0)
        >= 8
    ) & (high > platform_high) & (vol_ratio_ge_2(out))

    out["k_top_a_kill"] = (
        (high == hhv(high, 60).shift(1))
        & (amplitude <= -0.05)
        & (close < ma(close, 5).shift(1))
    )

    out["k_pat_dead"] = (
        (pct_chg_5 >= 10)
        & (body < atr * 0.3)
        & (high < hhv(high, 5).shift(1))
    )

    out["k_armor_lost"] = (
        (amplitude < 0.03).rolling(10, min_periods=10).sum().shift(1).fillna(0) >= 8
    ) & (amplitude <= -0.04) & (low < platform_low) & vol_ratio_ge_2(out)

    out["k_big_bull"] = (body >= atr * 1.5) & (body_pct >= 0.04)
    out["k_big_bear"] = (body >= atr * 1.5) & ((open_ - close) / pre_close >= 0.04)

    out["k_windmill"] = (
        (upper_shadow + lower_shadow >= body * 3) & (amplitude >= 0.05)
    )

    out["k_hammer"] = (lower_shadow >= body * 2) & (upper_shadow <= body * 0.5)

    # 4 position flags
    # 白线是 EMA(EMA(C,10),10)，不是 MA(C,10)。这里原来是全项目第三个
    # 互不相同的白线定义，现已统一到 tdx.zhixing_white。
    yellow = zhixing_yellow(close)
    white = zhixing_white(close)
    out["near_yellow_line"] = (close - yellow).abs() / yellow <= 0.02
    out["near_white_line"] = (close - white).abs() / white <= 0.02

    box_high = hhv(high, 20).shift(1)
    box_low = llv(low, 20).shift(1)
    out["breakout_platform"] = (high > box_high) & (box_high / box_low.replace(0, float("nan")) <= 1.10)

    vol_peaks = (out["vol"] == hhv(out["vol"], 60).shift(1)).astype(int)
    out["cover_trapped"] = False  # Requires chip distribution module; placeholder

    # Composite
    pattern_cols = [
        "k_v_reversal", "k_emergency_brake", "k_flat_thunder", "k_top_a_kill",
        "k_pat_dead", "k_armor_lost", "k_big_bull", "k_big_bear",
        "k_windmill", "k_hammer",
    ]
    pos_cols = ["near_yellow_line", "near_white_line", "breakout_platform", "cover_trapped"]
    has_pattern = out[pattern_cols].any(axis=1)
    has_position = out[pos_cols].any(axis=1)
    out["is_key_k"] = has_pattern & has_position

    return out


def vol_ratio_ge_2(df: pd.DataFrame) -> pd.Series:
    """Helper: volume ratio >= 2."""
    vol = pd.to_numeric(df["vol"], errors="coerce")
    return (vol / ref(vol, 1).replace(0, float("nan"))) >= 2.0


def ma(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin MA(X, N)."""
    return pd.to_numeric(series, errors="coerce").rolling(n, min_periods=1).mean()


def _avg_ma(close: pd.Series) -> pd.Series:
    """知行多空线。保留此名以兼容旧调用，实现已收敛到 tdx.zhixing_yellow。"""
    return zhixing_yellow(close)
