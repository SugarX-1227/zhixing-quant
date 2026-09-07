"""Dual line strategy: 5 gameplay modes + 5-step cycle."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from zhixing_quant.strategies.base import EntrySignal, ExitSignal, Strategy


class DualLineStrategy(Strategy):
    """双线战法.

    Modes (子玩法):
        1. 白线上穿黄线 (golden cross) + 放量
        2. 回踩黄线不破 + 缩量
        3. 回踩白线不破 + 缩量 (强势整理)
        4. 黄线拐头向上 + 白线跟随
        5. 黄线走平 + 白线上穿 (空中加油)

    Exit:
        - 跌破白线 且 次日未收回
        - 黄线拐头向下
    """

    name = "dual_line"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.allowed_regimes = ["BULL", "NEUTRAL"]

    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        if len(df) < 20:
            return None
        row = df.iloc[idx]
        golden_cross = bool(row.get("golden_cross", False))
        above_yellow = bool(row.get("above_yellow", False))
        vol = float(row["vol"])
        vol_prev = float(df.iloc[idx - 1]["vol"]) if idx > 0 else vol
        is_double_vol = vol >= vol_prev * 2.0

        if golden_cross and is_double_vol and above_yellow:
            return EntrySignal(
                code=str(row.get("code", "")),
                date=str(row.name)[:10] if hasattr(row, "name") else "",
                strategy="dual_line",
                price=float(row["close"]),
                stop_loss=float(row.get("white_line", row["low"])),
                take_profit=float(row["close"]) * 1.15,
                confidence=4,
                reason="dual_line_golden_cross",
            )
        return None

    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict, cfg: dict) -> Optional[ExitSignal]:
        row = df.iloc[idx]
        close = float(row["close"])
        white_line = float(row.get("white_line", close))
        if close < white_line and idx > 0:
            prev_close = float(df.iloc[idx - 1]["close"])
            prev_white = float(df.iloc[idx - 1].get("white_line", prev_close))
            if prev_close < prev_white:
                return ExitSignal(
                    code=pos.get("code", ""),
                    date=str(row.name)[:10] if hasattr(row, "name") else "",
                    price=close,
                    reason="dual_line_break_white",
                    priority=6,
                )
        return None
