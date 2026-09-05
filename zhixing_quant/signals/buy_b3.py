"""B3 continuation signal: small-body consolidation after B2 breakout."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import ref


def detect_b3(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append B3 continuation signal columns.

    Args:
        df: Daily OHLCV DataFrame with sig_b2 column.
        cfg: Config dictionary.

    Returns:
        Copy of df with B3 columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 4.4 节:
        - 在 sig_b2 后 1-3 个交易日内出现
        - 当日 K 实体小（实体 < ATR × 0.5）
        - vol < B2 当日 vol × 0.7（缩量）
        - close > B2 阳线中价
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    vol = pd.to_numeric(out["vol"], errors="coerce")
    body = (close - open_).abs()

    # ATR
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    pre_close = pd.to_numeric(ref(close, 1), errors="coerce")
    tr1 = high - low
    tr2 = (high - pre_close).abs()
    tr3 = (low - pre_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(14, min_periods=1).mean()

    # Find last sig_b2 within last 3 bars
    sig_b2 = out.get("sig_b2", pd.Series(False, index=out.index))
    b2_occurred = sig_b2.rolling(4, min_periods=1).max().shift(1).fillna(0).astype(bool)
    b2_vol = pd.Series(0.0, index=out.index)
    b2_mid = pd.Series(0.0, index=out.index)
    for i in range(len(out)):
        lookback = sig_b2.iloc[max(0, i - 3) : i + 1]
        if lookback.any():
            last_b2_idx = lookback[::-1].idxmax()
            pos = out.index.get_loc(last_b2_idx)
            b2_vol.iloc[i] = vol.iloc[pos]
            b2_mid.iloc[i] = (open_.iloc[pos] + close.iloc[pos]) / 2

    out["b3_small_body"] = body < atr_series * 0.5
    out["b3_volume_shrink"] = vol < b2_vol * 0.7
    out["b3_above_b2_mid"] = close > b2_mid
    out["sig_b3"] = b2_occurred & out["b3_small_body"] & out["b3_volume_shrink"] & out["b3_above_b2_mid"]
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range."""
    pre_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - pre_close).abs()
    tr3 = (low - pre_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=1).mean()
