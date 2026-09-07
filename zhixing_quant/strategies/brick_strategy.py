"""Brick strategy: N-type jump, flat jump, rising continuation + 4-brick stop."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from zhixing_quant.strategies.base import EntrySignal, ExitSignal, Strategy


class BrickStrategy(Strategy):
    """砖型图战法.

    Entry modes:
        - N型起跳: 昨天绿柱 + 今天红柱 + 高度达标 + 黄线达标
        - 横盘起跳: 连续红柱后缩量回踩 + 再放量突破
        - 上升延续: 红柱递增 + 不破黄线

    Exit:
        - 4-brick trailing stop: 跌破第4块砖低点
        - 禁区: 近20日涨幅 > 30% 不追
    """

    name = "brick"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.allowed_regimes = ["BULL", "NEUTRAL"]

    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        if "sig_brick" not in df.columns:
            return None
        row = df.iloc[idx]
        if not bool(row.get("sig_brick", False)):
            return None
        return EntrySignal(
            code=str(row.get("code", "")),
            date=str(row.name)[:10] if hasattr(row, "name") else "",
            strategy="brick",
            price=float(row["close"]),
            stop_loss=float(row.get("stop_loss", row["low"])),
            take_profit=float(row["close"]) * 1.15,
            confidence=4,
            reason="brick_entry",
        )

    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict, cfg: dict) -> Optional[ExitSignal]:
        row = df.iloc[idx]
        stop = pos.get("stop_loss")
        if stop and float(row["low"]) <= stop:
            return ExitSignal(
                code=pos.get("code", ""),
                date=str(row.name)[:10] if hasattr(row, "name") else "",
                price=float(row["low"]),
                reason="brick_stop",
                priority=2,
            )
        return None
