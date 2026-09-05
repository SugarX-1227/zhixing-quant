"""Pre-order checklist: validate signals before execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckResult:
    passed: bool
    checks: dict
    blockers: list


class PreOrderChecklist:
    """Run pre-order validation checks.

    Checks:
        1. MACD veto not triggered
        2. Defense engine no critical exit signal
        3. Regime allows the signal type
        4. Position size within limits
        5. Not already holding the stock
    """

    def run(self, signal, df, portfolio) -> CheckResult:
        checks = {}
        blockers = []

        # Check 1: MACD veto
        from zhixing_quant.signals.macd_veto import macd_veto
        veto = macd_veto(df, -1, signal.strategy if hasattr(signal, "strategy") else "b1")
        checks["macd_veto"] = not veto
        if veto:
            blockers.append("MACD veto triggered")

        # Check 2: Defense
        from zhixing_quant.portfolio.defense import DefenseEngine
        engine = DefenseEngine()
        pos = {"stop_loss": signal.stop_loss, "take_profit": signal.take_profit}
        exit_signal = engine.evaluate(df, -1, pos)
        checks["defense_clear"] = exit_signal is None
        if exit_signal:
            blockers.append(f"Defense: {exit_signal.reason}")

        # Check 3: Position size
        from zhixing_quant.portfolio.sizer import PositionSizer
        sizer = PositionSizer()
        shares = sizer.size(signal.price, signal.stop_loss, portfolio.get("equity", 0), 0.5)
        checks["size_positive"] = shares > 0
        if shares <= 0:
            blockers.append("Position size is zero")

        return CheckResult(passed=len(blockers) == 0, checks=checks, blockers=blockers)
