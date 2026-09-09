"""单针下30 战法（补票）。

规格来源：06-strategies.md S5 · 单针下20 / 下30
    长下影探至短期均线下方 30% 后收回，视为主力洗盘挖坑，次日补票。
    纪律 `[LOCKED]`：只做一次，不补第二次；跌破针尖即离场。

止损取针尖（当日最低价），这是这套战法的定义性风险位。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from zhixing_quant.strategies.base import EntrySignal, ExitSignal, Strategy


class SingleNeedleStrategy(Strategy):
    name = "single_needle"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.allowed_regimes = ["BULL", "NEUTRAL"]

    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        if len(df) < 120:
            return None
        row = df.iloc[idx]
        if not bool(row.get("sig_single_needle_30", False)):
            return None
        close = float(row["close"])
        low = float(row["low"])
        if low >= close:
            return None
        return EntrySignal(
            code=str(row.get("code", "")),
            date=str(row.name)[:10],
            strategy="single_needle",
            price=close,
            stop_loss=low,              # 针尖
            take_profit=close * 1.10,
            confidence=3,
            reason="single_needle_30",
        )

    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict,
                        cfg: dict) -> Optional[ExitSignal]:
        row = df.iloc[idx]
        stop = pos.get("stop_loss")
        if stop and float(row["low"]) <= float(stop):
            return ExitSignal(
                code=pos.get("code", ""), date=str(row.name)[:10],
                price=float(stop), reason="single_needle_break_tip", priority=2,
            )
        return None
