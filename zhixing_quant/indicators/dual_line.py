"""Dual line (white/yellow) indicator with regime and cross detection."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import cross, ema, ma


def add_dual_line(df: pd.DataFrame, fast: int = 10, slow: int = 20) -> pd.DataFrame:
    """Append dual line columns.

    Args:
        df: Daily OHLCV DataFrame.
        fast: White line period (default 10, matches 知行短期趋势线).
        slow: Yellow line period (default 20).

    Returns:
        Copy of df with dual line columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 3.1 节:
        - 白线: MA10 (short-term trend)
        - 黄线: MA20 (main cost line)
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")

    white_line = ema(close, fast)
    yellow_line = ma(close, slow)

    out["white_line"] = white_line
    out["yellow_line"] = yellow_line
    out["regime_strong"] = white_line > yellow_line
    out["regime_weak"] = white_line < yellow_line
    out["golden_cross"] = cross(white_line, yellow_line)
    out["death_cross"] = cross(yellow_line, white_line)
    out["above_yellow"] = close > yellow_line
    out["above_white"] = close > white_line

    return out
