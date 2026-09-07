"""Daily workflow: timing → strategy mode → review holdings → scan → execute plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionPlan:
    date: str
    regime: str
    regime_strength: float
    mode: str
    actions: list


def run_daily_workflow(date: str, cfg: dict) -> ExecutionPlan:
    """Run the 5-step daily workflow.

    Steps:
        1. Timing: compute active_value proxy → update regime
        2. Strategy mode: map regime to allowed signals and position caps
        3. Review holdings: rate existing positions, trigger defense exits
        4. Scan universe: run signal detectors on watchlist
        5. Generate execution plan: entries, exits, position sizes

    Args:
        date: Trading date string.
        cfg: Configuration dictionary.

    Returns:
        ExecutionPlan for the day.
    """
    # Step 1: Timing
    from zhixing_quant.timing.active_value import active_value_proxy
    from zhixing_quant.timing.regime import RegimeStateMachine
    from zhixing_quant.timing.strategy_mode import get_strategy_mode

    proxy = active_value_proxy(date, cfg)
    sm = RegimeStateMachine()
    regime_state = sm.update(proxy["composite_score"])

    # Step 2: Strategy mode
    mode = get_strategy_mode(regime_state["current_regime"], regime_state["regime_strength"])

    # Step 3-5: simplified — in production, these integrate with portfolio and scanner
    actions = []
    if mode["allow_open"]:
        for sig in mode["allowed_signals"]:
            actions.append(f"SCAN:{sig}")

    return ExecutionPlan(
        date=date,
        regime=regime_state["current_regime"],
        regime_strength=regime_state["regime_strength"],
        mode=mode["mode"],
        actions=actions,
    )
