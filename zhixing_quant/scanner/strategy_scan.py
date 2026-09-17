"""把任意注册战法适配成扫描器。

原来每个战法都要手写一个 `scanner/daily_*.py`，所以只有 brick / b1 / b2 三个
有扫描器，dual_line / yoga_pants / single_needle 虽然实现了却永远扫不到。

这些战法都实现了统一接口：

    Strategy.entry_conditions(df, idx, cfg) -> EntrySignal | None

所以只需要一个适配器：跑对应的指标流水线，在最后一根 K 线上调 entry_conditions，
把返回的 EntrySignal 摊平成扫描器需要的列。新增战法时只要在 registry 注册，
再在 PIPELINES 里声明它要哪些指标，扫描器自动就有了。

注意：只在最后一根 K 线上求值，因为盘后选股只关心"今天有没有信号"。
回测需要逐根求值，走的是另一条路（backtest/engine.py 读预先算好的信号列）。
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from zhixing_quant.indicators.pipeline import PIPELINES, run_pipeline
from zhixing_quant.scanner._core import StrategySpec, scan

# 战法 → 中文名 / 所属体系。规格 01.6 要求超短与波段分账户。
STRATEGY_META: Dict[str, dict] = {
    "brick":         {"label": "砖型图",   "book": "scalp", "spec": "S4"},
    "b1":            {"label": "B1 阶段低点", "book": "swing", "spec": "04.2"},
    "b2":            {"label": "B2 确认阳线", "book": "swing", "spec": "04.3"},
    "single_needle": {"label": "单针下30", "book": "scalp", "spec": "S5"},
}

SIGNAL_COL = "sig_strategy"


def _annotate(df: pd.DataFrame, cfg: dict, strategy) -> pd.DataFrame:
    """在最后一根 K 线上求值 entry_conditions，把结果摊平成列。"""
    out = df.copy()
    out[SIGNAL_COL] = False
    # 不要覆盖指标层已经算好的止损/止盈（砖型图就自带 stop_loss 列）。
    # 只在缺列时建列，有值时由下面的 EntrySignal 择优覆盖。
    for col in ("stop_loss", "take_profit"):
        if col not in out.columns:
            out[col] = float("nan")
    out["confidence"] = 0
    out["signal_note"] = ""

    if len(out) < 2:
        return out
    try:
        sig = strategy.entry_conditions(out, -1, cfg)
    except Exception:
        return out
    if sig is None:
        return out

    i = out.index[-1]
    out.loc[i, SIGNAL_COL] = True
    if getattr(sig, "stop_loss", None):
        out.loc[i, "stop_loss"] = float(sig.stop_loss)
    if getattr(sig, "take_profit", None):
        out.loc[i, "take_profit"] = float(sig.take_profit)
    out.loc[i, "confidence"] = int(getattr(sig, "confidence", 0) or 0)
    out.loc[i, "signal_note"] = str(getattr(sig, "reason", "") or "")
    return out


# 信号内部代号 → 人话。界面上不该出现 dual_line_golden_cross 这种东西。
REASON_TEXT = {
    "brick_entry": "昨天绿柱 + 今天红柱 + 红柱高度达标 + 收盘站上知行多空线",
    "b1_stage_low": "J值超卖 + 站上知行多空线 + 短期趋势线确认 + 涨跌幅在 ±4% 内",
    "b2_confirm": "B1 之后的确认阳线 + 放量",
    "single_needle_30": "单针下探超过 30%，长下影收回",
}


def build_spec(name: str, cfg: dict) -> Optional[StrategySpec]:
    """按战法名生成扫描规格。未注册或没有指标流水线时返回 None。"""
    from zhixing_quant.strategies.registry import get_strategy

    strategy = get_strategy(name, cfg)
    if strategy is None:
        return None
    pipeline_name = name if name in PIPELINES else None
    if pipeline_name is None:
        return None

    meta = STRATEGY_META.get(name, {})

    def add_indicators(df: pd.DataFrame, c: dict) -> pd.DataFrame:
        result = run_pipeline(df, c, pipeline_name)
        return _annotate(result.df, c, strategy)

    def extra_fields(row: pd.Series) -> dict:
        out = {}
        for col, key in (("yellow_line", "yellow_line"), ("white_line", "white_line"),
                         ("kdj_j", "kdj_j"), ("red_streak", "red_streak")):
            if col in row.index and pd.notna(row[col]):
                out[key] = float(row[col]) if col != "red_streak" else int(row[col])
        for col in ("stop_loss", "take_profit"):
            v = row.get(col)
            if v is not None and pd.notna(v):
                out[col] = float(v)
        if "confidence" in row.index:
            out["confidence"] = int(row["confidence"])
        return out

    def reason(row: pd.Series, c: dict) -> str:
        note = str(row.get("signal_note", "") or "")
        return REASON_TEXT.get(note, note or meta.get("label", name))

    return StrategySpec(
        key=name,
        label=meta.get("label", name),
        signal_col=SIGNAL_COL,
        add_indicators=add_indicators,
        min_bars=lambda c: 120,          # 黄线含 MA114，规格 00.3 要求 ≥120 根
        extra_fields=extra_fields,
        reason=reason,
    )


def scan_strategy(
    name: str,
    cfg: dict,
    end_date: Optional[str] = None,
    limit_universe: Optional[int] = None,
    progress=None,
):
    """扫描指定战法。返回 (候选 DataFrame, {code: 带指标的K线})。"""
    spec = build_spec(name, cfg)
    if spec is None:
        raise ValueError(
            f"战法 {name} 不可扫描。已注册的战法：{sorted(available())}"
        )
    return scan(spec, cfg, end_date=end_date, limit_universe=limit_universe,
                progress=progress)


def available() -> Dict[str, dict]:
    """当前可扫描的战法。既要在 registry 注册，也要有指标流水线。"""
    from zhixing_quant.strategies.registry import STRATEGY_REGISTRY

    return {
        name: {**STRATEGY_META.get(name, {"label": name, "book": "swing"}),
               "registered": True}
        for name in STRATEGY_REGISTRY
        if name in PIPELINES
    }
