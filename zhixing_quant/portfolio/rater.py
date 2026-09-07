"""Holding rater: 0-5 star rating for current positions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RatingResult:
    stars: int
    reasons: list
    risk_level: str


class HoldingRater:
    """Rate a holding from 0 to 5 stars.

    Rule source:
        Z哥战法第7章 - 持仓评级:
        - 5 stars: strong trend, above yellow, volume healthy, MACD bullish
        - 3 stars: neutral, near yellow line, mixed signals
        - 0 stars: danger, below yellow, MACD bearish, distribution present
    """

    def rate(self, df, idx: int, pos: dict) -> RatingResult:
        """Rate the current holding.

        Args:
            df: DataFrame with indicators and signals.
            idx: Current bar index.
            pos: Position info dict.

        Returns:
            RatingResult with stars (0-5), reasons list, and risk_level.
        """
        row = df.iloc[idx]
        close = float(row["close"])
        yellow_line = float(row.get("yellow_line", close))
        white_line = float(row.get("white_line", close))

        score = 5
        reasons = []

        # Deductions
        if bool(row.get("sig_distribution", False)):
            score -= 3
            reasons.append("distribution_detected")
        if bool(row.get("false_death_cross", False)):
            score -= 2
            reasons.append("macd_death_cross")
        if bool(row.get("sig_s1", False)) or bool(row.get("sig_s2", False)) or bool(row.get("sig_s3", False)):
            score -= 2
            reasons.append("sell_signal")
        if close < yellow_line:
            score -= 2
            reasons.append("below_yellow")
        elif close < white_line:
            score -= 1
            reasons.append("below_white")
        if bool(row.get("dist_stagnation", False)):
            score -= 1
            reasons.append("volume_stagnation")

        stars = max(0, min(5, score))
        risk_level = self._risk_label(stars)
        return RatingResult(stars=stars, reasons=reasons, risk_level=risk_level)

    @staticmethod
    def _risk_label(stars: int) -> str:
        if stars >= 4:
            return "LOW"
        if stars == 3:
            return "MEDIUM"
        if stars == 2:
            return "HIGH"
        return "CRITICAL"
