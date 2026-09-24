"""业务对象 → 前端 JSON。只做形状转换，不做业务判断。"""

from __future__ import annotations

import dataclasses
from typing import Dict, Optional

import numpy as np
import pandas as pd

from zhixing_quant.ui import param_schema as PS
from zhixing_quant.web.labels import PRIORITY_LABEL, exit_reason
from zhixing_quant.web.serialize import frame, jsonable, records, scalar, series

# 买点标记按这个顺序取第一个有命中的信号列
BUY_COLS = ("sig_strategy", "sig_brick", "sig_b1", "sig_b2")


def is_flag(s: pd.Series) -> bool:
    """布尔信号列（允许夹杂 NaN）。"""
    v = s.dropna()
    return len(v) > 0 and bool(v.map(lambda x: isinstance(x, (bool, np.bool_))).all())


def param(p: PS.Param, cfg: dict) -> dict:
    """参数声明 + 当前配置里的实际值。"""
    out = jsonable(dataclasses.asdict(p))
    out["value"] = scalar(PS.get_value(cfg, p.key, p.default))
    if p.kind == "choice":
        out["choice_labels"] = {str(c): PS.kind_label(c) for c in p.choices}
    return out


def kline(df: pd.DataFrame, days: Optional[int] = None) -> dict:
    """K线 + 均线/多空线/白线 + 买点 + 砖型图，ECharts 直接可用的形状。

    均线在全长序列上算完再截尾，否则显示窗口的前 19 根 MA20 是空的。
    """
    from zhixing_quant.indicators.tdx import ma

    d = df.copy()
    d["_ma20"] = ma(d["close"], 20)
    if days:
        d = d.tail(int(days))
    dates = [i.strftime("%Y-%m-%d") for i in d.index]

    def col(name):
        return [scalar(v) for v in d[name]] if name in d.columns else None

    buys = []
    for c in BUY_COLS:
        if c in d.columns:
            hit = d[d[c].fillna(False).astype(bool)]
            if not hit.empty:
                buys = [[i.strftime("%Y-%m-%d"), scalar(v * 0.97)]
                        for i, v in zip(hit.index, hit["low"])]
                break

    brick = None
    if "brick_value" in d.columns:
        # 通达信 STICKLINE：每根砖从前一日值画到当日值。首日没有前值不画。
        prev = d["brick_value"].shift(1)
        brick = [[scalar(p), scalar(c), scalar(min(p, c)), scalar(max(p, c))]
                 if pd.notna(p) and pd.notna(c) else [None, None, None, None]
                 for p, c in zip(prev, d["brick_value"])]

    return {
        "dates": dates,
        "ohlc": [[scalar(o), scalar(c), scalar(lo), scalar(h)]      # ECharts 顺序
                 for o, c, lo, h in zip(d["open"], d["close"], d["low"], d["high"])],
        "vol": col("vol"),
        "ma20": [scalar(v) for v in d["_ma20"]],
        "yellow": col("yellow_line"),
        "white": col("white_line"),
        "buys": buys,
        "brick": brick,
    }


def review(v) -> dict:
    out = jsonable(v)
    out["action_text"] = exit_reason(v.action_reason) if v.action else ""
    out["priority_label"] = (PRIORITY_LABEL.get(v.action_priority, f"P{v.action_priority}")
                             if v.action else "")
    return out


def daily(result) -> dict:
    return {
        "date": result.date, "book": result.book,
        "regime": result.regime, "regime_score": scalar(result.regime_score),
        "regime_strength": scalar(result.regime_strength),
        "regime_trigger": getattr(result, "regime_trigger", ""),
        "mode": result.mode, "allow_open": bool(result.allow_open),
        "max_total_pct": scalar(result.max_total_pct),
        "position_pct": scalar(result.position_pct),
        "cash": scalar(result.cash), "equity": scalar(result.equity),
        "reviews": [review(v) for v in result.reviews],
        "actions_needed": len(result.actions_needed),
        "defense_coverage": jsonable(result.defense_coverage),
        "offense_disabled_reason": result.offense_disabled_reason,
        "candidates": candidates(result.candidates),
        "plans": [jsonable(p) for p in result.plans],
        "notes": list(result.notes),
    }


def candidates(df: pd.DataFrame) -> dict:
    return {
        "rows": records(df),
        "warnings": list(df.attrs.get("warnings", [])) if df is not None else [],
        "rank_note": df.attrs.get("rank_note", "") if df is not None else "",
    }


def backtest(run, strategy: str, changed: Dict, n_switches: Optional[int]) -> dict:
    m = run.metrics
    trades = run.trades
    reasons = []
    if trades is not None and not trades.empty and "exit_reason" in trades.columns:
        reasons = [[str(k), int(v)] for k, v in trades["exit_reason"].value_counts().items()]
        trades = trades.sort_values("entry_date", ascending=False)

    regime_log = getattr(run, "regime_log", None)
    log = None
    if regime_log is not None and not regime_log.empty:
        show = regime_log.copy()
        show["trade_date"] = pd.to_datetime(
            show["trade_date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
        show.columns = ["日期", "活跃市值", "当日涨跌", "触发依据", "触发后区间"]
        log = frame(show)

    return {
        "strategy": strategy,
        "metrics": jsonable(m),
        "excess_return": scalar(run.excess_return),
        "equity": series(run.equity_curve),
        "benchmark": series(run.benchmark) if run.benchmark is not None else None,
        "drawdown": series(run.drawdown),
        "trades": frame(trades),
        "exit_reasons": reasons,
        "regime_log": log,
        "warnings": list(run.warnings),
        "universe_note": run.universe_note,
        "loaded": run.loaded, "skipped": run.skipped,
        "entry_note": getattr(run, "entry_note", ""),
        "exit_note": getattr(run, "exit_note", ""),
        "changed": jsonable(changed),
        "active_switches": n_switches,
    }
