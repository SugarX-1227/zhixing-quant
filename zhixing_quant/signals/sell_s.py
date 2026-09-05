"""S1/S2/S3 sell signal series: risk release, trend end, abnormal move."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import ref


def detect_s_series(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append S1/S2/S3 sell signal columns.

    Args:
        df: Daily OHLCV DataFrame with MACD, dual_line, etc.
        cfg: Config dictionary.

    Returns:
        Copy of df with sell signal columns.

    Rule source:
        通达信行情指标与选股指标(1).md + Z哥战法第4章:
        - S1 (止损): price breaks below yellow line or prior low + volume expansion
        - S2 (止盈): break below white line + death cross acceleration
        - S3 (危险): long bearish candle or gap-down panic
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    vol = pd.to_numeric(out["vol"], errors="coerce")
    pre_close = pd.to_numeric(ref(close, 1), errors="coerce")

    yellow_line = pd.to_numeric(out.get("yellow_line", close), errors="coerce")
    white_line = pd.to_numeric(out.get("white_line", close), errors="coerce")
    vol_prev = pd.to_numeric(ref(vol, 1), errors="coerce")

    body = (close - open_).abs()
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low
    is_big_bear = (
        (close < open_)
        & (body >= (high - low) * 0.6)
        & (upper_shadow < body * 0.3)
    )
    is_double_vol = vol / vol_prev.replace(0, float("nan")) >= 2.0

    # S1: break below yellow line or recent 5-day low + volume
    recent_low = low.rolling(5, min_periods=1).min()
    break_yellow = (close < yellow_line) & is_double_vol
    break_low = close < recent_low
    out["s1_risk_release"] = (break_yellow | break_low) & (body > 0)
    out["sig_s1"] = out["s1_risk_release"]

    # S2: break below white line with MACD death cross acceleration
    below_white = close < white_line
    macd_death = out.get("false_death_cross", pd.Series(False, index=out.index))
    out["s2_trend_end"] = below_white & macd_death
    out["sig_s2"] = out["s2_trend_end"]

    # S3: long bearish candle or gap-down > 2%
    gap_down = (open_ < pre_close * 0.98) & (close < open_)
    out["s3_abnormal"] = is_big_bear & is_double_vol | gap_down
    out["sig_s3"] = out["s3_abnormal"]

    out["sig_s_stop"] = low
    return out
