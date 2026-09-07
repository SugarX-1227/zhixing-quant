"""Violent K-line signal: extreme breakout at bottom."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import hhv, llv, ref


def detect_violent_k(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append violent K-line signal columns.

    Args:
        df: Daily OHLCV DataFrame.
        cfg: Config dictionary.

    Returns:
        Copy of df with violent K columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 4.6 节:
        条件①: 过去60日close处于价格区间 [min, min + (max-min)*0.3]
        条件②: 前5日平均振幅 < 2.5%，当日振幅 > 5%
        条件③: k_big_bull AND is_double_vol AND 上影 < 实体 * 0.3
        条件④: 下面三选一
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    vol = pd.to_numeric(out["vol"], errors="coerce")
    pre_close = pd.to_numeric(ref(close, 1), errors="coerce")

    body = (close - open_).abs()
    body_pct = body / open_.replace(0, float("nan"))
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    amplitude = (high - low) / pre_close.replace(0, float("nan"))

    price_min = llv(close, 60).shift(1)
    price_max = hhv(close, 60).shift(1)
    price_range = price_max - price_min
    out["vk_bottom_position"] = close <= (price_min + price_range * 0.3)

    avg_amp_5 = amplitude.rolling(5, min_periods=1).mean().shift(1)
    out["vk_sudden_move"] = (avg_amp_5 < 0.025) & (amplitude > 0.05)

    vol_prev = ref(vol, 1)
    is_double = vol / vol_prev.replace(0, float("nan")) >= 2.0
    out["vk_big_bull_vol"] = (body >= amplitude * 1.5) & (body_pct >= 0.04) & is_double & (upper_shadow < body * 0.3)

    # Condition 4: simplified — check if near 60-day high pressure or recent panic
    near_high = (close >= price_max * 0.97) & (close <= price_max * 1.03)
    recent_panic = amplitude <= -0.05
    out["vk_scary_position"] = near_high | recent_panic

    out["sig_violent_k_small"] = (
        out["vk_bottom_position"] & out["vk_sudden_move"] & out["vk_big_bull_vol"]
    )
    out["sig_violent_k_large"] = (
        out["sig_violent_k_small"] & out["vk_scary_position"]
    )
    out["sig_violent_k_stop"] = low
    return out
