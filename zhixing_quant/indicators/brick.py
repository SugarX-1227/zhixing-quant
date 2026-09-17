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
        通达信行情指标与选股指标(1).md「1-砖型图短期选股」（使用者每日实跑的公式）:
        - 黄线 = (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4
        - 砖型图 = IF(VAR6A>4, VAR6A-4, 0)
        - XG = 昨天绿柱 AND 今天红柱 AND 红柱高度>=绿柱高度*2/3 AND 黄线达标
    """
    out = df.copy()
    brick_cfg = cfg["brick"]
    n1 = int(brick_cfg["n1"])
    n2 = int(brick_cfg["n2"])
    min_height_ratio = float(brick_cfg.get("min_height_ratio", 2.0 / 3.0))
    min_height = float(brick_cfg.get("min_brick_height", 0.0))
    min_growth = float(brick_cfg.get("min_brick_growth", 0.0))
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

    signal_cols = brick_signal_columns(out["brick_value"], min_height_ratio,
                                       min_height, min_growth)
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
                         min_height_ratio: float = 2.0 / 3.0,
                         min_brick_height: float = 0.0,
                         min_brick_growth: float = 0.0) -> pd.DataFrame:
    """构造「绿转强红」的判据列，逐行对应使用者每天在通达信里跑的选股公式。

    权威来源（2026-09 由使用者提供，即他每日实跑的那份）::

        今天红柱 := 砖型图 > REF(砖型图,1);
        昨天绿柱 := REF(砖型图,1) < REF(砖型图,2);
        红柱高度 := 砖型图 - REF(砖型图,1);
        绿柱高度 := REF(砖型图,2) - REF(砖型图,1);
        高度达标 := 红柱高度 >= 绿柱高度 * 2/3;
        XG: 昨天绿柱 AND 今天红柱 AND 高度达标 AND 黄线达标;

    仓库内 `通达信行情指标与选股指标(1).md` 与 `知行量化系统开发规划.docx`
    3.6 节（`green_to_strong_red: 红砖覆盖前一根绿砖 2/3 以上`）两份文档
    与之完全一致。

    ⚠️ 2026-09 之前这里实现的是另一套判据::

        strong_abs := 砖型图 > 4
        strong_rel := 砖型图 > REF(砖型图,1) * 1.5

    出处标的是 `Z哥战法-完整战法详解.md 附录 B.5`，而那份文档**不在本仓库**，
    与仓库内两份源文档均不符。它错在两处：

    1. **比较对象错了。** 公式比的是「红柱高度 vs 绿柱高度」——两个**增量**；
       改成了比「砖型图 vs 昨日砖型图」——两个**水平值**。
    2. **阈值 4 用重了。** `砖型图 := IF(VAR6A>4, VAR6A-4, 0)` 里已经扣过一次 4，
       再要求 `砖型图 > 4` 等于要求 `VAR6A > 8`。

    后果实测（50 只 × 2.8 年）：

        现实现（砖高>4 且 >前值×1.5）        1 次信号
        通达信公式（红柱高度 >= 绿柱高度×2/3）  1553 次信号（≈558 次/年）

    规划书 6.8 验收标准写的是「砖型图战法触发频率约 200-400 次/年」。
    砖型图在实盘里本该是信号最多的一套战法，旧实现把它压成了哑战法。
    原因也很直白：砖型图实测常年在 24~154 之间，`> 4` 几乎恒真、不起过滤作用，
    而 `> 前值 × 1.5` 是要求一条双重平滑的慢速振荡指标单日跳涨 50%，
    在红柱里只有 0.3% 的日子能满足。

    Args:
        brick_value: 砖高序列（已经是 max(VAR6A-4, 0)）。
        min_height_ratio: 红柱高度 / 绿柱高度 的下限，通达信公式为 2/3。
        min_brick_height: **可选**附加门槛，砖高绝对下限。0 = 关闭。
            不属于通达信公式，留作实验用。
        min_brick_growth: **可选**附加门槛，砖高相对昨日的倍数下限。0 = 关闭。

    Returns:
        含 brick_today_red / brick_yesterday_green / brick_height_ok 等列。

    Rule source:
        通达信行情指标与选股指标(1).md「1-砖型图短期选股」
        知行量化系统开发规划.docx 3.6 / 6.8
    """
    prev = ref(brick_value, 1)
    prev2 = ref(brick_value, 2)

    today_red = brick_value > prev
    # 通达信是严格小于。用 ~ref(today_red,1) 代替会把「昨日持平」也算成绿柱。
    yesterday_green = prev < prev2

    red_height = brick_value - prev
    green_height = prev2 - prev
    height_ok = red_height >= green_height * min_height_ratio

    # 以下两道是可选附加门槛，默认关闭（0），不影响通达信口径
    strong_abs = (brick_value > min_brick_height if min_brick_height > 0
                  else pd.Series(True, index=brick_value.index))
    strong_rel = (brick_value > prev * min_brick_growth if min_brick_growth > 0
                  else pd.Series(True, index=brick_value.index))

    return pd.DataFrame(
        {
            "brick_today_red": today_red.fillna(False),
            "brick_yesterday_green": yesterday_green.fillna(False),
            "brick_red_height": red_height,
            "brick_green_height": green_height,
            "brick_prev_height": prev,
            "brick_strong_abs": strong_abs.fillna(False),
            "brick_strong_rel": strong_rel.fillna(False),
            "brick_height_ok": (height_ok & strong_abs & strong_rel).fillna(False),
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
