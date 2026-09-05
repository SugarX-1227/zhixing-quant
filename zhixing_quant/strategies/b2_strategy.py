"""B2 strategy: 3 modes — parallel heavy cannon, post-disaster rebuild, eager to try."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from zhixing_quant.strategies.base import EntrySignal, ExitSignal, Strategy


class B2Strategy(Strategy):
    """三大B2战法.

    Modes:
        1. 平行重炮: sig_b2 + vol > 2x average + price near yellow line
        2. 灾后重建: sig_b2 after a drop > 8% + recovery pattern
        3. 跃跃欲试: sig_b2 + close < white_line but above yellow + volume drying up
    """

    name = "b2"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.allowed_regimes = ["BULL", "NEUTRAL"]

    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        if len(df) < 20:
            return None
        row = df.iloc[idx]
        if not bool(row.get("sig_b2", False)):
            return None

        vol = float(row["vol"])
        vol_ma20 = vol / max(float(df["vol"].rolling(20, min_periods=1).mean().iloc[idx]), 1)
        close = float(row["close"])
        yellow = float(row.get("yellow_line", close))
        white = float(row.get("white_line", close))

        if vol_ma20 >= 2.0 and close >= yellow:
            mode = "parallel_heavy_cannon"
            confidence = 5
            stop = yellow * 0.98
        elif vol_ma20 < 1.0 and close > yellow:
            mode = "eager_to_try"
            confidence = 3
            stop = yellow * 0.98
        else:
            mode = "post_disaster"
            confidence = 3
            stop = float(row["low"]) * 0.99

        return EntrySignal(
            code=str(row.get("code", "")),
            date=str(row.name)[:10] if hasattr(row, "name") else "",
            strategy=f"b2_{mode}",
            price=close,
            stop_loss=stop,
            take_profit=close * 1.15,
            confidence=confidence,
            reason=f"b2_{mode}",
        )

    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict, cfg: dict) -> Optional[ExitSignal]:
        row = df.iloc[idx]
        stop = pos.get("stop_loss")
        if stop and float(row["low"]) <= stop:
            return ExitSignal(
                code=pos.get("code", ""),
                date=str(row.name)[:10] if hasattr(row, "name") else "",
                price=float(row["low"]),
                reason="b2_stop",
                priority=2,
            )
        return None
