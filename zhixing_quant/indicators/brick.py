"""Brick chart indicator translated from the local TongDaXin formula."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import average_ma, hhv, llv, ref, sma_tdx


def add_brick_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Append yellow line and brick selection columns.

    Args:
        df: Daily OHLCV DataFrame with open/high/low/close/amount.
        cfg: Config dictionary.

    Returns:
        Copy of df with TDX-derived columns.

    Rule source:
        通达信行情指标与选股指标(1).md:
        - 黄线 = (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4
        - XG = 昨天绿柱 AND 今天红柱 AND 高度达标 AND 黄线达标
    """
    out = df.copy()
    brick_cfg = cfg["brick"]
    n1 = int(brick_cfg["n1"])
    n2 = int(brick_cfg["n2"])
    min_height_ratio = float(brick_cfg["min_height_ratio"])
    close = out["close"]
    high = out["high"]
    low = out["low"]

    out["yellow_line"] = average_ma(close, brick_cfg["yellow_ma_windows"])
    out["above_yellow"] = close > out["yellow_line"]

    denominator_1 = hhv(high, n1) - llv(low, n1) + 0.001
    var1a = (hhv(high, n1) - close) / denominator_1 * 100 - 90
    var2a = sma_tdx(var1a, n1, 1) + 100
    var3a = (close - llv(low, n1)) / denominator_1 * 100
    var4a = sma_tdx(var3a, n2, 1)
    var5a = sma_tdx(var4a, n2, 1) + 100
    var6a = var5a - var2a
    out["brick_value"] = (var6a - 4).clip(lower=0)

    signal_cols = brick_signal_columns(out["brick_value"], min_height_ratio)

    out["brick_today_red"] = signal_cols["brick_today_red"]
    out["brick_yesterday_green"] = signal_cols["brick_yesterday_green"]
    out["brick_red_height"] = signal_cols["brick_red_height"]
    out["brick_green_height"] = signal_cols["brick_green_height"]
    out["brick_height_ok"] = signal_cols["brick_height_ok"]
    out["sig_brick"] = (
        out["brick_yesterday_green"]
        & out["brick_today_red"]
        & out["brick_height_ok"]
        & out["above_yellow"]
    )
    out["red_streak"] = _red_streak(out["brick_today_red"])
    out["stop_loss"] = out["low"]
    out["abandon_gap_up_price"] = out["close"] * (1 + float(cfg["execution"]["abandon_gap_up"]))
    return out


def brick_signal_columns(brick_value: pd.Series, min_height_ratio: float) -> pd.DataFrame:
    """Build the TongDaXin brick XG helper columns from brick values.

    Args:
        brick_value: Computed brick chart values.
        min_height_ratio: Required red-height / green-height ratio.

    Returns:
        DataFrame with red/green/height helper columns.

    Rule source:
        通达信行情指标与选股指标(1).md:
        今天红柱:=砖型图 > REF(砖型图,1);
        昨天绿柱:=REF(砖型图,1) < REF(砖型图,2);
        高度达标:=红柱高度 >= 绿柱高度 * 2/3;
    """
    today_red = brick_value > ref(brick_value, 1)
    yesterday_green = ref(brick_value, 1) < ref(brick_value, 2)
    red_height = brick_value - ref(brick_value, 1)
    green_height = ref(brick_value, 2) - ref(brick_value, 1)
    height_ok = red_height >= green_height * min_height_ratio
    return pd.DataFrame(
        {
            "brick_today_red": today_red.fillna(False),
            "brick_yesterday_green": yesterday_green.fillna(False),
            "brick_red_height": red_height,
            "brick_green_height": green_height,
            "brick_height_ok": height_ok.fillna(False),
        },
        index=brick_value.index,
    )


def _red_streak(today_red: pd.Series) -> pd.Series:
    streak = []
    count = 0
    for is_red in today_red.fillna(False):
        if bool(is_red):
            count += 1
        else:
            count = 0
        streak.append(count)
    return pd.Series(streak, index=today_red.index)
