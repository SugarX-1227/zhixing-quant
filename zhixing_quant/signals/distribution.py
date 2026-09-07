"""Distribution detection: 5 main-force unloading patterns."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import hhv, llv, ref


def detect_distribution(df: pd.DataFrame, market_cap: float, cfg: dict) -> pd.DataFrame:
    """Append distribution pattern columns.

    Args:
        df: Daily OHLCV DataFrame with dual_line, volume_price, etc.
        market_cap: Market cap in CNY (used for liquidity threshold).
        cfg: Config dictionary.

    Returns:
        Copy of df with distribution columns.

    Rule source:
        Z哥战法第5章 - 主力出货识别:
        - Type 1: 高位放量滞涨 (stagnation at high with volume)
        - Type 2: 天量天价 (record volume at record high)
        - Type 3: 黄昏之星 (evening star top reversal)
        - Type 4: 均线失守 (break below key MA support)
        - Type 5: 长阴破位 (long bearish candle breaking support)
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    vol = pd.to_numeric(out["vol"], errors="coerce")

    yellow_line = pd.to_numeric(out.get("yellow_line", close), errors="coerce")
    white_line = pd.to_numeric(out.get("white_line", close), errors="coerce")

    body = (close - open_).abs()
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low
    price_change = close.pct_change()

    high_60 = hhv(high, 60)
    low_60 = llv(low, 60)
    near_high = close >= high_60 * 0.97
    vol_ma5 = vol.rolling(5, min_periods=1).mean()
    high_vol = vol >= vol_ma5 * 1.8

    # Type 1: 高位放量滞涨 — high position + high volume + weak price movement
    out["dist_stagnation"] = near_high & high_vol & (price_change.abs() < 0.02) & (body < (high - low) * 0.3)

    # Type 2: 天量天价 — volume > 2x 20-day avg + price at 60-day high
    vol_ma20 = vol.rolling(20, min_periods=1).mean()
    out["dist_sky_high"] = (vol >= vol_ma20 * 2.0) & near_high

    # Type 3: 黄昏之星 — yesterday long bullish + today long bearish (evening star)
    pre_close_val = pd.to_numeric(ref(close, 1), errors="coerce")
    pre_open = pd.to_numeric(ref(open_, 1), errors="coerce")
    pre_body = (pre_close_val - pre_open).abs()
    pre_bull = (pre_close_val > pre_open) & (pre_body >= (pd.to_numeric(ref(high, 1), errors="coerce") - pd.to_numeric(ref(low, 1), errors="coerce")) * 0.6)
    today_bear = (close < open_) & (body >= (high - low) * 0.6) & (upper_shadow < body * 0.3)
    out["dist_evening_star"] = pre_bull & today_bear & near_high

    # Type 4: 均线失守 — close < yellow line with volume expansion
    out["dist_ma_break"] = (close < yellow_line) & (vol > vol_ma5 * 1.5)

    # Type 5: 长阴破位 — long bearish candle breaks below recent low
    out["dist_long_bear"] = (close < open_) & (body >= (high - low) * 0.7) & (close < low_60 * 1.05)

    out["sig_distribution"] = (
        out["dist_stagnation"]
        | out["dist_sky_high"]
        | out["dist_evening_star"]
        | out["dist_ma_break"]
        | out["dist_long_bear"]
    )
    out["sig_distribution_type"] = "none"
    mask_stag = out["dist_stagnation"]
    mask_sky = out["dist_sky_high"]
    mask_eve = out["dist_evening_star"]
    mask_ma = out["dist_ma_break"]
    mask_bear = out["dist_long_bear"]
    out.loc[mask_stag, "sig_distribution_type"] = "stagnation"
    out.loc[mask_sky, "sig_distribution_type"] = "sky_high"
    out.loc[mask_eve, "sig_distribution_type"] = "evening_star"
    out.loc[mask_ma, "sig_distribution_type"] = "ma_break"
    out.loc[mask_bear, "sig_distribution_type"] = "long_bear"
    return out
