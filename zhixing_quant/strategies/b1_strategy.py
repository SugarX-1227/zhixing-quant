"""B1 战法：阶段性低点。

规格来源：04-signals-buy.md 4.2 B1 — 阶段性低点（最安全、盈亏比最高）
    四个条件全部必需：J < 13、收盘 > 知行多空线、短期趋势线 > 多空线、
    涨跌幅在 ±4% 之间。

止损取当日最低价（跌破入场日低点即离场，见 07 防守阶梯 P2）。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from zhixing_quant.strategies.base import EntrySignal, ExitSignal, Strategy


class B1Strategy(Strategy):
    name = "b1"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.allowed_regimes = ["BULL", "NEUTRAL"]

    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        if len(df) < 120:
            return None
        row = df.iloc[idx]
        if not bool(row.get("sig_b1", False)):
            return None
        close = float(row["close"])
        return EntrySignal(
            code=str(row.get("code", "")),
            date=str(row.name)[:10],
            strategy="b1",
            price=close,
            stop_loss=float(row["low"]),
            take_profit=close * 1.15,
            confidence=5,          # 规格 04.2 称其盈亏比最高
            reason="b1_stage_low",
        )

    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict,
                        cfg: dict) -> Optional[ExitSignal]:
        row = df.iloc[idx]
        close = float(row["close"])
        yellow = float(row.get("yellow_line", close))
        if close < yellow:
            return ExitSignal(
                code=pos.get("code", ""), date=str(row.name)[:10], price=close,
                reason="b1_break_yellow", priority=7,
            )
        return None
