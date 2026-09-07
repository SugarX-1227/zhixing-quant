"""Market regime state machine: BULL / NEUTRAL / BEAR with transition rules."""

from __future__ import annotations


class RegimeStateMachine:
    """State machine for market regime classification.

    Rule source:
        Z哥战法第6章 - 多空区间状态机:
        - BULL:  active_value > 0.65 for 3+ consecutive days
        - BEAR:  active_value < 0.35 for 3+ consecutive days
        - NEUTRAL: otherwise

    Transitions:
        NEUTRAL → BULL:  score hits BULL threshold
        NEUTRAL → BEAR:  score hits BEAR threshold
        BULL → NEUTRAL:  score drops below 0.60
        BEAR → NEUTRAL:  score rises above 0.40
    """

    def __init__(self, bull_threshold: float = 0.65, bear_threshold: float = 0.35):
        self.bull_threshold = bull_threshold
        self.bear_threshold = bear_threshold
        self.current_regime = "NEUTRAL"
        self.days_in_regime = 0
        self.regime_strength = 0.5

    def update(self, proxy_value: float) -> dict:
        """Update regime based on new proxy value.

        Args:
            proxy_value: Composite active value score in [0, 1].

        Returns:
            Dict with current_regime, days_in_regime, regime_strength.
        """
        prev = self.current_regime

        if self.current_regime == "BULL":
            if proxy_value < 0.60:
                self.current_regime = "NEUTRAL"
                self.days_in_regime = 0
            else:
                self.days_in_regime += 1
                self.regime_strength = min(1.0, 0.5 + self.days_in_regime * 0.05)

        elif self.current_regime == "BEAR":
            if proxy_value > 0.40:
                self.current_regime = "NEUTRAL"
                self.days_in_regime = 0
            else:
                self.days_in_regime += 1
                self.regime_strength = min(1.0, 0.5 + self.days_in_regime * 0.05)

        else:  # NEUTRAL
            if proxy_value >= self.bull_threshold:
                self.current_regime = "BULL"
                self.days_in_regime = 1
                self.regime_strength = 0.5
            elif proxy_value <= self.bear_threshold:
                self.current_regime = "BEAR"
                self.days_in_regime = 1
                self.regime_strength = 0.5
            else:
                self.days_in_regime += 1
                self.regime_strength = max(0.0, 0.5 - self.days_in_regime * 0.02)

        return {
            "current_regime": self.current_regime,
            "days_in_regime": self.days_in_regime,
            "regime_strength": round(self.regime_strength, 4),
        }

    def reset(self):
        """Reset state machine to initial neutral state."""
        self.current_regime = "NEUTRAL"
        self.days_in_regime = 0
        self.regime_strength = 0.5
