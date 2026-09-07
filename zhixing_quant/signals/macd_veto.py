"""MACD veto function for signal filtering."""

from __future__ import annotations

import pandas as pd


def macd_veto(df: pd.DataFrame, idx: int, signal_type: str = "b1") -> bool:
    """Check if MACD vetoes the signal at the given index.

    Args:
        df: DataFrame with MACD columns (dif, dea, bull_divergence, etc.).
        idx: Current bar index (typically -1 for latest).
        signal_type: Signal type for context ("b1", "b2", "sb1", etc.).

    Returns:
        True if the signal should be vetoed.

    Rule source:
        通达信行情指标与选股指标(1).md 第 3.5 节:
        - 多头信号 + bear_divergence → 否决
        - 多头信号 + false_golden_cross → 否决
        - 多头信号 + above_zero == False 且非超级B1 → 否决
    """
    row = df.iloc[idx]
    if not bool(row.get("above_zero", True)):
        if signal_type != "sb1":
            return True
    if bool(row.get("bear_divergence", False)):
        return True
    if bool(row.get("false_golden_cross", False)):
        return True
    return False
