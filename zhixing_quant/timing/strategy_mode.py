"""Strategy mode mapping: regime → allowed signals and position sizing."""

from __future__ import annotations


def get_strategy_mode(regime: str, regime_strength: float = 0.5) -> dict:
    """Return strategy mode parameters for the given market regime.

    Args:
        regime: Current regime ("BULL", "NEUTRAL", "BEAR").
        regime_strength: Confidence of the regime classification.

    Returns:
        Dict with mode, allow_open, max_position_pct, allowed_signals.

    Rule source:
        Z哥战法第6章 - 策略模式映射:
        - BULL:  all signals, max 80% position
        - NEUTRAL: B1/SB1/B2 only, max 50% position
        - BEAR:  no new opens, only defense
    """
    if regime == "BULL":
        return {
            "mode": "BULL",
            "allow_open": True,
            "max_position_pct": 0.80,
            "allowed_signals": ["b1", "sb1", "b2", "b3", "violent_k", "single_needle", "brick"],
        }
    if regime == "BEAR":
        return {
            "mode": "BEAR",
            "allow_open": False,
            "max_position_pct": 0.0,
            "allowed_signals": [],
        }
    # NEUTRAL
    return {
        "mode": "NEUTRAL",
        "allow_open": True,
        "max_position_pct": 0.50,
        "allowed_signals": ["b1", "sb1", "b2", "single_needle"],
    }
