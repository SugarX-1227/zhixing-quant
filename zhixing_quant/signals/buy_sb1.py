"""SB1 super B1:洗盘买点 after前期异动."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import ref


def detect_sb1(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append SB1 super B1 signal columns.

    Args:
        df: Daily OHLCV DataFrame with is_double_vol, yellow_line, etc.
        cfg: Config dictionary.

    Returns:
        Copy of df with SB1 columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 4.5 节:
        - 前 N=20 日出现过 is_double_vol（前期异动）
        - 缩量跌破短线对手盘止损位（关键K低点/黄线/整数关口）
        - 跌破后 1-3 个交易日内底部筹码柱不动
        - 快速企稳回到对手盘上方（close >= 击穿前止损位）
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    vol = pd.to_numeric(out["vol"], errors="coerce")
    yellow_line = pd.to_numeric(out.get("yellow_line", close), errors="coerce")

    double_vol = out.get("is_double_vol", pd.Series(False, index=out.index))
    had_double_vol = double_vol.rolling(20, min_periods=1).max().shift(1).fillna(False).astype(bool)

    vol_prev = ref(vol, 1)
    shrink_break = (vol < vol_prev) & (close < yellow_line) & (close < ref(close, 1))

    # Recover within 3 days: close >= yellow_line after a shrink_break
    broke = shrink_break.astype(int)
    recovered = pd.Series(False, index=out.index)
    for i in range(1, len(out)):
        if bool(broke.iloc[i - 1]):
            for j in range(i, min(i + 4, len(out))):
                if close.iloc[j] >= yellow_line.iloc[j]:
                    recovered.iloc[i] = True
                    break

    out["had_double_vol_20d"] = had_double_vol
    out["sb1_shrink_break"] = shrink_break
    out["sig_sb1"] = had_double_vol & shrink_break & recovered
    out["sig_sb1_stop"] = out.get("low", close) * 0.999
    return out
