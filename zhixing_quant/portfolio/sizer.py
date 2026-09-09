"""仓位计算：风险优先，择时封顶。

规格来源：08-position-sizing.md 仓位铁律 `[LOCKED]`
    单笔风险敞口 = 股数 × (买入价 - 止损价) ≤ 权益 × risk_per_trade

修正记录：
    原实现 `kelly = risk_pct / risk_per_share` 后又乘了一次 entry_price，
    把股数放大约 1000 倍，导致 `min(kelly_shares, max_shares)` 永远取择时上限。
    实测三种价位（10 / 100 / 1400 元）算出的市值都是 250,000，正好等于
    equity × regime_max_pct —— 风险控制那一半从未生效，每笔都是满仓。
    现改回标准的风险头寸公式。
"""

from __future__ import annotations


class PositionSizer:
    """按单笔风险敞口定股数，再用择时上限和单票上限封顶。

    Rule source:
        08-position-sizing.md:
        - 单笔风险 ≤ 权益 × risk_per_trade（默认 2%）
        - kelly_fraction 作为安全系数进一步缩小（默认 0.25）
        - 择时决定总仓位上限：牛 0.80 / 中性 0.50 / 空 0.0
        - A 股 100 股整手
    """

    def __init__(self, risk_pct: float = 0.02, kelly_fraction: float = 0.25):
        self.risk_pct = float(risk_pct)
        self.kelly_fraction = float(kelly_fraction)

    def size(
        self,
        entry_price: float,
        stop_loss: float,
        equity: float,
        regime_max_pct: float = 1.0,
        per_position_pct: float = 1.0,
    ) -> int:
        """算出应买股数。

        Args:
            entry_price: 计划买入价。
            stop_loss: 止损价，必须低于买入价。
            equity: 账户总权益。
            regime_max_pct: 择时允许的总仓位上限（0.0-1.0）。空头传 0 直接不开仓。
            per_position_pct: 单票市值占权益的上限（0.0-1.0），见配置
                `portfolio.per_position_pct`。

        Returns:
            股数，向下取整到 100 的倍数。任一约束不满足时返回 0。
        """
        if entry_price <= 0 or stop_loss <= 0 or equity <= 0:
            return 0
        if stop_loss >= entry_price:
            return 0
        if regime_max_pct <= 0:
            return 0

        risk_per_share = entry_price - stop_loss

        # 1. 风险头寸：单笔亏损不超过 权益 × risk_pct × kelly_fraction
        risk_budget = equity * self.risk_pct * self.kelly_fraction
        shares = risk_budget / risk_per_share

        # 2. 单票市值上限
        shares = min(shares, equity * per_position_pct / entry_price)

        # 3. 择时总仓位上限
        shares = min(shares, equity * regime_max_pct / entry_price)

        return max(0, int(shares // 100) * 100)

    def risk_exposure(self, shares: int, entry_price: float, stop_loss: float) -> float:
        """这笔单子打到止损会亏多少钱。用于下单前核对。"""
        return max(0.0, (float(entry_price) - float(stop_loss)) * int(shares))
