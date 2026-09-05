"""Yoga pants strategy: B1 + SB1 + B3 combination play."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from zhixing_quant.strategies.base import EntrySignal, ExitSignal, Strategy


class YogaPantsStrategy(Strategy):
    """瑜伽裤战法 — B1/SB1 买点 + B3 中继加仓.

    Logic:
        - Primary entry: sig_b1 or sig_sb1
        - Confirmation: sig_b3 within 3 days of entry
        - Exit: defense engine 8 rules
    """

    name = "yoga_pants"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.allowed_regimes = ["BULL", "NEUTRAL"]

    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        if len(df) < 20:
            return None
        row = df.iloc[idx]
        has_b1 = bool(row.get("sig_b1", False))
        has_sb1 = bool(row.get("sig_sb1", False))
        has_b3 = bool(row.get("sig_b3", False))

        if has_b1 or has_sb1:
            confidence = 5 if has_sb1 else 3
            stop = float(row.get("sig_sb1_stop", row["low"])) if has_sb1 else float(row["low"])
            return EntrySignal(
                code=str(row.get("code", "")),
                date=str(row.name)[:10] if hasattr(row, "name") else "",
                strategy="yoga_pants_primary",
                price=float(row["close"]),
                stop_loss=stop,
                take_profit=float(row["close"]) * 1.15,
                confidence=confidence,
                reason="yoga_pants_b1_or_sb1",
            )

        if has_b3:
            return EntrySignal(
                code=str(row.get("code", "")),
                date=str(row.name)[:10] if hasattr(row, "name") else "",
                strategy="yoga_pants_addon",
                price=float(row["close"]),
                stop_loss=float(row["low"]),
                take_profit=float(row["close"]) * 1.10,
                confidence=2,
                reason="yoga_pants_b3_confirm",
            )
        return None

    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict, cfg: dict) -> Optional[ExitSignal]:
        row = df.iloc[idx]
        stop = pos.get("stop_loss")
        if stop and float(row["low"]) <= stop:
            return ExitSignal(
                code=pos.get("code", ""),
                date=str(row.name)[:10] if hasattr(row, "name") else "",
                price=float(row["low"]),
                reason="yoga_pants_stop",
                priority=2,
            )
        return None
