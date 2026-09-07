"""TongDaXin-compatible indicator helpers."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def ref(series: pd.Series, n: int = 1) -> pd.Series:
    """TongDaXin REF(X, N): previous N bars."""
    return series.shift(n)


def hhv(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin HHV(X, N): rolling highest value."""
    return series.rolling(n, min_periods=1).max()


def llv(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin LLV(X, N): rolling lowest value."""
    return series.rolling(n, min_periods=1).min()


def sma_tdx(series: pd.Series, n: int, m: int) -> pd.Series:
    """TongDaXin SMA(X, N, M).

    Args:
        series: Input numeric series.
        n: TDX smoothing period.
        m: TDX weight.

    Returns:
        Smoothed series where Y[t] = (M*X[t] + (N-M)*Y[t-1]) / N.
    """
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    prev = np.nan
    for i, value in enumerate(values):
        if np.isnan(value):
            out[i] = prev
            continue
        if np.isnan(prev):
            prev = value
        else:
            prev = (m * value + (n - m) * prev) / n
        out[i] = prev
    return pd.Series(out, index=series.index)


def ma(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin MA(X, N): simple moving average."""
    return series.rolling(n, min_periods=1).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """TongDaXin EMA(X, N): exponential moving average."""
    return pd.to_numeric(series, errors="coerce").ewm(span=span, adjust=False).mean()


def average_ma(series: pd.Series, windows: Iterable[int]) -> pd.Series:
    """Average several TDX MA lines into one line."""
    lines = [ma(series, int(window)) for window in windows]
    return sum(lines) / len(lines)


def exist(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin EXIST(X, N): True if X was True within the last N bars."""
    return series.rolling(n, min_periods=1).max().astype(bool)


def cross(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """TongDaXin CROSS(A, B): A crosses above B on the current bar."""
    a = pd.to_numeric(series_a, errors="coerce")
    b = pd.to_numeric(series_b, errors="coerce")
    return (a > b) & (a.shift(1) <= b.shift(1))


def effective_color(open_: pd.Series, close: pd.Series, pre_close: pd.Series) -> pd.Series:
    """Fake-yang-real-yin rule: close > open but close < pre_close counts as bear."""
    up = close > open_
    fake_yang = (close >= open_) & (close < pre_close)
    return pd.Series(np.where(fake_yang, "bear", np.where(up, "bull", "bear")), index=close.index)

