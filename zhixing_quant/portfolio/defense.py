"""Defense engine: 8-priority rule evaluation for exit signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExitSignal:
    reason: str
    priority: int
    action: str = "SELL"
    stop_price: Optional[float] = None


class DefenseEngine:
    """Evaluate 8 defense priorities in order; first match triggers exit.

    Rule source:
        Z哥战法第7章 - 防守8条:
        P1: 触发止盈位 (take profit hit)
        P2: 触发止损位 (stop loss hit)
        P3: S1/S2/S3 卖点 (sell signal)
        P4: 主力出货 (distribution detected)
        P5: MACD死叉加速 (MACD death cross acceleration)
        P6: 跌破白线 (break below white line)
        P7: 跌破黄线 (break below yellow line)
        P8: 放量滞涨 (high volume stagnation)
    """

    def evaluate(self, df, idx: int, pos: dict) -> Optional[ExitSignal]:
        """Check all defense rules in priority order.

        Args:
            df: DataFrame with all indicators and signals.
            idx: Current bar index (typically -1 for latest).
            pos: Position dict with entry_price, stop_loss, take_profit, etc.

        Returns:
            ExitSignal if any rule triggers, else None.
        """
        return self.evaluate_row(df.iloc[idx], pos)

    def evaluate_row(self, row, pos: dict) -> Optional[ExitSignal]:
        """Same ladder, but against an already-extracted row.

        回测主循环每根 K 线都要走一遍阶梯，`df.iloc[idx]` 每次构造一个
        Series 是纯开销。出场规则层已经拿到行了，直接传进来。
        """
        checks = [
            self._check_take_profit(row, pos),
            self._check_stop_loss(row, pos),
            self._check_sell_signals(row),
            self._check_distribution(row),
            self._check_macd_death(row),
            self._check_white_break(row),
            self._check_yellow_break(row),
            self._check_volume_stagnation(row),
        ]
        for signal in checks:
            if signal is not None:
                return signal
        return None

    def _check_take_profit(self, row, pos) -> Optional[ExitSignal]:
        tp = pos.get("take_profit")
        if tp is not None and float(row["high"]) >= tp:
            return ExitSignal(reason="take_profit_hit", priority=1, stop_price=tp)
        return None

    def _check_stop_loss(self, row, pos) -> Optional[ExitSignal]:
        sl = pos.get("stop_loss")
        if sl is not None and float(row["low"]) <= sl:
            return ExitSignal(reason="stop_loss_hit", priority=2, stop_price=sl)
        return None

    def _check_sell_signals(self, row) -> Optional[ExitSignal]:
        if bool(row.get("sig_s1", False)):
            return ExitSignal(reason="s1_risk_release", priority=3)
        if bool(row.get("sig_s2", False)):
            return ExitSignal(reason="s2_trend_end", priority=3)
        if bool(row.get("sig_s3", False)):
            return ExitSignal(reason="s3_abnormal", priority=3)
        return None

    def _check_distribution(self, row) -> Optional[ExitSignal]:
        if bool(row.get("sig_distribution", False)):
            d_type = row.get("sig_distribution_type", "unknown")
            return ExitSignal(reason=f"distribution_{d_type}", priority=4)
        return None

    def _check_macd_death(self, row) -> Optional[ExitSignal]:
        if bool(row.get("false_death_cross", False)):
            return ExitSignal(reason="macd_death_cross", priority=5)
        return None

    def _check_white_break(self, row) -> Optional[ExitSignal]:
        close = float(row["close"])
        white_line = float(row.get("white_line", close))
        if close < white_line:
            return ExitSignal(reason="below_white_line", priority=6)
        return None

    def _check_yellow_break(self, row) -> Optional[ExitSignal]:
        close = float(row["close"])
        yellow_line = float(row.get("yellow_line", close))
        if close < yellow_line:
            return ExitSignal(reason="below_yellow_line", priority=7)
        return None

    def _check_volume_stagnation(self, row) -> Optional[ExitSignal]:
        if bool(row.get("dist_stagnation", False)):
            return ExitSignal(reason="volume_stagnation", priority=8)
        return None
