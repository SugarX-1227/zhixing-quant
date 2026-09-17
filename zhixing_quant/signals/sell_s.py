"""S1/S2/S3 卖点：风险释放 / 趋势终结 / 异动。

修正记录（2026-09 审计）：

1. **S1 的「跌破前低」分支恒为假。** 原实现

       recent_low = low.rolling(5, min_periods=1).min()   # 窗口含当日
       break_low  = close < recent_low

   rolling 窗口把今天自己也算进去了，于是 recent_low <= 今日 low <= 今日 close，
   这个条件在数学上永远不成立。不是"很少触发"，是**一次都不会触发**。
   S1 因此退化成只剩"跌破黄线且放量"一条。改成看**不含今日**的前 N 日最低。

2. **config 的 sell.* 三个阈值从未被读到。** 2.0 / 0.6 / 0.02 全部硬编码在
   函数体里，settings.yaml 里那三行改成任何值都不会有任何反应，而界面上
   看不出来。现在全部从 cfg 读，缺省值与原硬编码值一致，行为不变。
"""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import ref

# 缺省值与修正前硬编码的数值一致，只传 {} 时行为不变。
DEFAULTS = {
    "s1_double_vol_threshold": 2.0,   # 放量倍数
    "s1_break_low_window": 5,         # 前低回看根数（不含当日）
    "s1_break_low_needs_volume": False,  # 跌破前低是否也要求放量
    "s3_big_bear_body_min": 0.6,      # 大阴线实体占全幅下限
    "s3_gap_down_pct": 0.02,          # 低开幅度
}


def detect_s_series(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """追加 S1/S2/S3 卖点列。

    Args:
        df: 日线 OHLCV，需含 yellow_line / white_line / false_death_cross
            才能判全三条；缺列时对应分支静默退化（见下）。
        cfg: 配置字典，读 cfg["sell"]，缺省见 DEFAULTS。

    Returns:
        df 的副本，含 sig_s1 / sig_s2 / sig_s3 / sig_s_stop。

    Rule source:
        通达信行情指标与选股指标(1).md + Z哥战法第4章:
        - S1 (风险释放): 放量跌破黄线，或跌破前 N 日最低
        - S2 (趋势终结): 跌破白线 + MACD 死叉加速
        - S3 (异动):     放量大阴线，或恐慌低开
    """
    s = {**DEFAULTS, **((cfg or {}).get("sell", {}) or {})}
    vol_mult = float(s["s1_double_vol_threshold"])
    low_window = int(s["s1_break_low_window"])
    low_needs_vol = bool(s["s1_break_low_needs_volume"])
    body_min = float(s["s3_big_bear_body_min"])
    gap_pct = float(s["s3_gap_down_pct"])

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
    is_big_bear = (
        (close < open_)
        & (body >= (high - low) * body_min)
        & (upper_shadow < body * 0.3)
    )
    is_double_vol = vol / vol_prev.replace(0, float("nan")) >= vol_mult

    # S1: 放量跌破黄线，或跌破前 N 日最低。
    # shift(1) 是这条规则的关键：窗口必须**不含当日**，否则恒不成立。
    prev_low = low.shift(1).rolling(low_window, min_periods=1).min()
    break_yellow = (close < yellow_line) & is_double_vol
    break_low = close < prev_low
    if low_needs_vol:
        break_low = break_low & is_double_vol
    out["s1_break_yellow"] = break_yellow.fillna(False)
    out["s1_break_prev_low"] = break_low.fillna(False)
    out["s1_risk_release"] = ((break_yellow | break_low) & (body > 0)).fillna(False)
    out["sig_s1"] = out["s1_risk_release"]

    # S2: 跌破白线 + MACD 死叉加速
    below_white = close < white_line
    macd_death = out.get("false_death_cross", pd.Series(False, index=out.index))
    out["s2_trend_end"] = (below_white & macd_death).fillna(False)
    out["sig_s2"] = out["s2_trend_end"]

    # S3: 放量大阴线，或低开超阈值
    gap_down = (open_ < pre_close * (1 - gap_pct)) & (close < open_)
    out["s3_abnormal"] = ((is_big_bear & is_double_vol) | gap_down).fillna(False)
    out["sig_s3"] = out["s3_abnormal"]

    out["sig_s_stop"] = low
    return out
