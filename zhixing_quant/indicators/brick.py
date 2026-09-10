"""Brick chart indicator translated from the local TongDaXin formula."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import (attach_zhixing_lines, hhv, llv, ref, sma_tdx)


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
    min_height = float(brick_cfg.get("min_brick_height", 4.0))
    min_growth = float(brick_cfg.get("min_brick_growth", 1.5))
    close = out["close"]
    high = out["high"]
    low = out["low"]

    attach_zhixing_lines(out, cfg)

    denominator_1 = hhv(high, n1) - llv(low, n1) + 0.001
    var1a = (hhv(high, n1) - close) / denominator_1 * 100 - 90
    var2a = sma_tdx(var1a, n1, 1) + 100
    var3a = (close - llv(low, n1)) / denominator_1 * 100
    var4a = sma_tdx(var3a, n2, 1)
    var5a = sma_tdx(var4a, n2, 1) + 100
    var6a = var5a - var2a
    out["brick_value"] = (var6a - 4).clip(lower=0)

    signal_cols = brick_signal_columns(out["brick_value"], min_height, min_growth)
    for col in signal_cols.columns:
        out[col] = signal_cols[col]
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


def brick_signal_columns(brick_value: pd.Series,
                         min_height: float = 4.0,
                         min_growth: float = 1.5) -> pd.DataFrame:
    """构造「绿转强红」的判据列。

    ⚠️ 这里曾经踩中战法文档 5.6 专门加粗警告过的那个误读。

    原实现：

        red_height   = 砖高 - REF(砖高,1)          # 今日涨幅
        green_height = REF(砖高,2) - REF(砖高,1)   # 昨日跌幅
        height_ok    = 今日涨幅 >= 昨日跌幅 * 2/3

    这来自课程口语"红柱覆盖前一根绿柱 2/3 以上"。但作者在公式注释里写得
    很明确：**"今天的红柱高度 > 昨天数值的 1.5 倍（即增长超过 50%），
    防止微红盘。"** 比较的是砖高本身，不是涨幅比跌幅。

    附录 B.5 的原始判据：

        基础拐点 := REF(AA,1)=0 AND AA=1        （昨天不是红砖，今天是）
        强度确认 := 砖型图 > 4 AND 砖型图 > REF(砖型图,1) * 1.5

    原实现漏掉的两件事：
    1. `砖高 > 4` 这道**绝对强度**门槛完全没有实现。砖高从 0.1 涨到 0.11
       也会被判成红砖，正是作者说的"微红盘"。
    2. `× 1.5` 的比较对象错了。

    实测同一组数据：文档定义命中 0 次，原实现命中 19 次。差的不是精度，
    是在放行大量贴地的微弱信号——而瑜伽裤战法只有这一个信号。

    Args:
        brick_value: 砖高序列。
        min_height: 绝对强度下限，文档为 4。
        min_growth: 相对昨日砖高的倍数下限，文档为 1.5。

    Returns:
        含 brick_today_red / brick_yesterday_green / brick_height_ok 等列。

    Rule source:
        Z哥战法-完整战法详解.md 附录 B.5、5.6 节
    """
    prev = ref(brick_value, 1)
    today_red = brick_value > prev
    # 基础拐点：昨天不是红砖（绿砖或 0），今天变成红砖
    yesterday_green = ~ref(today_red, 1).fillna(False).astype(bool)

    strong_abs = brick_value > min_height
    strong_rel = brick_value > prev * min_growth
    height_ok = strong_abs & strong_rel

    return pd.DataFrame(
        {
            "brick_today_red": today_red.fillna(False),
            "brick_yesterday_green": yesterday_green,
            "brick_red_height": brick_value - prev,
            "brick_prev_height": prev,
            "brick_strong_abs": strong_abs.fillna(False),
            "brick_strong_rel": strong_rel.fillna(False),
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
