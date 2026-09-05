"""Position sizing: Kelly-inspired with regime-aware caps."""

from __future__ import annotations

import math


class PositionSizer:
    """Size positions based on risk, regime, and account equity.

    Rule source:
        Z哥战法第7章 - 仓位管理:
        - Risk per trade: max 2% of equity
        - Round to 100 shares (A-share lot size)
        - Respect regime max_position_pct
        - Kelly criterion with safety factor 0.25
    """

    def __init__(self, risk_pct: float = 0.02, kelly_fraction: float = 0.25):
        self.risk_pct = risk_pct
        self.kelly_fraction = kelly_fraction

    def size(
        self,
        entry_price: float,
        stop_loss: float,
        equity: float,
        regime_max_pct: float = 1.0,
    ) -> int:
        """Calculate number of shares to buy.

        Args:
            entry_price: Intended entry price.
            stop_loss: Stop-loss price.
            equity: Total account equity.
            regime_max_pct: Max position percentage allowed by regime (0.0-1.0).

        Returns:
            Number of shares, rounded down to nearest 100.
        """
        if entry_price <= 0 or stop_loss <= 0 or equity <= 0:
            return 0
        if entry_price <= stop_loss:
            return 0

        risk_per_share = entry_price - stop_loss
        if risk_per_share <= 0:
            return 0

        # Kelly-inspired sizing
        kelly = self.risk_pct / risk_per_share
        shares = int(kelly * equity * entry_price * self.kelly_fraction)

        # Apply regime cap
        max_shares = int(equity * regime_max_pct / entry_price)
        shares = min(shares, max_shares)

        # Round down to lot size (100 shares)
        shares = (shares // 100) * 100
        return max(0, shares)
