"""活跃市值择时：用指南针 0AMV 单日涨跌划分多头 / 空头区间。

规则（战法口径，2026-09 与使用者确认）：
- 空头区间：单个交易日活跃市值跌幅超过 -2.3%。禁止开仓，已有持仓清仓。
- 强势多头：单个交易日涨幅超过 +4%。次日的进攻动作（买前一天资金流入
  最多的板块）属于战法层，需要板块资金流数据，系统目前只标注区间。
- 多头最低标准：连续 3 天全部上涨，且单日涨幅**简单相加**超过 +4%。
  例：+2%、+1%、+1.5% 记 4.5%，达标；+2%、-0.2%、+3% 有一天下跌，不算。
- 区间是持续态：触发空头后保持空头，直到出现多头触发；反之亦然。
  从未触发过时为中性。
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

import pandas as pd

# 默认阈值，可在 config/settings.yaml 的 timing.active_value 下覆盖
BEAR_DROP = -0.023
BULL_SURGE = 0.04
BULL_3DAY_SUM = 0.04

STRENGTH = {"BEAR": 1.0, "BULL": 0.7, "NEUTRAL": 0.5}


def oamv_trigger_states(
    cfg: Optional[dict] = None, end_date: Optional[int] = None,
) -> pd.DataFrame:
    """逐日判断活跃市值区间，返回 DataFrame：

        trade_date  收盘日期
        close       活跃市值收盘
        pct         当日涨跌幅（简单涨跌幅）
        trigger     触发说明，空字符串表示当日无新触发
        regime      当日**收盘后**所处的区间（BEAR/BULL/NEUTRAL，持续态）

    注意 regime 是「当日收盘后」的状态，次日开盘才按它交易——
    回测/实盘用 regime_before 做平移，避免用当日收盘判当日（未来函数）。
    """
    from zhixing_quant.data.oamv import load_oamv

    tcfg = (cfg or {}).get("timing", {}).get("active_value", {})
    bear_drop = float(tcfg.get("bear_drop", BEAR_DROP))
    bull_surge = float(tcfg.get("bull_surge", BULL_SURGE))
    bull_3day = float(tcfg.get("bull_3day_sum", BULL_3DAY_SUM))

    df = load_oamv(end_date=end_date)
    if len(df) < 4:
        raise RuntimeError(
            "活跃市值历史不足 4 天，无法判断多空区间。"
            "先运行 python -m zhixing_quant.data.sync --oamv（需退出指南针）。"
        )

    pct = df["close"].pct_change()
    up3 = (pct > 0) & (pct.shift(1) > 0) & (pct.shift(2) > 0)
    sum3 = pct + pct.shift(1) + pct.shift(2)

    regimes, triggers, strongs = [], [], []
    cur, strong = "NEUTRAL", False
    for i in range(len(df)):
        p = pct.iloc[i]
        trigger = ""
        if p < bear_drop:
            cur, strong = "BEAR", True
            trigger = "单日跌幅超2.3%"
        elif p > bull_surge:
            cur, strong = "BULL", True
            trigger = "单日涨幅超4%"
        elif bool(up3.iloc[i]) and float(sum3.iloc[i]) > bull_3day:
            cur, strong = "BULL", False
            trigger = "三日连涨和超4%"
        regimes.append(cur)
        triggers.append(trigger)
        strongs.append(strong)

    out = pd.DataFrame({
        "trade_date": df["trade_date"].astype(int).to_numpy(),
        "close": df["close"].to_numpy(),
        "pct": pct.to_numpy(),
        "trigger": triggers,
        "strong": strongs,
        "regime": regimes,
    })
    return out


def regime_before(
    days: Iterable, states: Optional[pd.DataFrame] = None, cfg: Optional[dict] = None,
) -> Dict[str, str]:
    """把「收盘后状态」平移成「该交易日开盘时可知的区间」。

    交易日 T 的区间 = T 之前最后一个 OAMV 交易日收盘后的状态。
    返回 {"YYYY-MM-DD": regime}，键与回测引擎的日历一致。
    """
    if states is None:
        states = oamv_trigger_states(cfg)
    tdates = states["trade_date"].to_numpy()
    regimes = states["regime"].to_numpy()
    out: Dict[str, str] = {}
    for d in days:
        ts = pd.Timestamp(d)
        key = ts.strftime("%Y-%m-%d")
        if key in out:
            continue
        t = int(ts.strftime("%Y%m%d"))
        idx = tdates.searchsorted(t, side="left")     # 第一个 >= t 的位置
        out[key] = str(regimes[idx - 1]) if idx > 0 else "NEUTRAL"
    return out


def current_regime(date: Optional[str] = None, cfg: Optional[dict] = None) -> Dict:
    """最新可得的区间状态（供盘后决策与界面标注）。

    Args:
        date: 只使用该日期及之前的数据（"20260917" 或 "2026-09-17"），
            缺省用库里全部数据。当天数据未同步时自动落到上一交易日。

    Returns:
        {regime, strength, date, oamv, pct, trigger}
        strength 仅用于展示：BEAR/单日暴涨触发的 BULL 为 1.0，
        三日规则触发的 BULL 为 0.7，NEUTRAL 为 0.5。
    """
    end = int(str(date).replace("-", "")) if date else None
    states = oamv_trigger_states(cfg, end_date=end)
    last = states.iloc[-1]
    regime = str(last["regime"])
    strength = 1.0 if (regime == "BEAR" or last["strong"]) else STRENGTH[regime]
    return {
        "regime": regime,
        "strength": strength,
        "date": int(last["trade_date"]),
        "oamv": float(last["close"]),
        "pct": float(last["pct"]) if pd.notna(last["pct"]) else 0.0,
        "trigger": str(last["trigger"]),
    }
