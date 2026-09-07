"""Active value proxy: market-wide sentiment and liquidity indicators."""

from __future__ import annotations

import random


def active_value_proxy(date: str, cfg: dict) -> dict:
    """Compute active value proxy for the given date.

    Args:
        date: Date string in YYYY-MM-DD format.
        cfg: Config dictionary with proxy thresholds.

    Returns:
        Dict with proxy components and composite score.

    Rule source:
        Z哥战法第6章 - 择时层:
        - northbound_inflow_5d: 北向资金净流入(5日)
        - turnover_ma_change: 两市成交额MA变化率
        - main_force_inflow_5d: 主力净流入(5日)
        - margin_change: 融资余额变化率
        - advance_decline_ratio: 涨家数/跌家数比
        - chi_next_sh_ratio: 创业板/上证强弱比
        - new_high_20d_pct: 20日新高占比

    Note:
        Real implementation requires Level-2 / fund-flow data feeds.
        This proxy uses simulated values for development and backtesting.
    """
    thresholds = cfg.get("timing", {}).get("active_value", {})
    default_score = thresholds.get("default_score", 0.5)

    # In production, replace these with real data sources (e.g., TDX MCP)
    proxy = {
        "northbound_inflow_5d": _simulated_northbound(date),
        "turnover_ma_change": _simulated_turnover(date),
        "main_force_inflow_5d": _simulated_main_force(date),
        "margin_change": _simulated_margin(date),
        "advance_decline_ratio": _simulated_advance_decline(date),
        "chi_next_sh_ratio": _simulated_chi_next_sh(date),
        "new_high_20d_pct": _simulated_new_high(date),
    }

    # Composite score: weighted average of normalized components
    score = (
        _norm(proxy["northbound_inflow_5d"], -50, 50)
        + _norm(proxy["turnover_ma_change"], -0.2, 0.2)
        + _norm(proxy["main_force_inflow_5d"], -30, 30)
        + _norm(proxy["margin_change"], -0.05, 0.05)
        + _norm(proxy["advance_decline_ratio"], 0.3, 3.0)
        + _norm(proxy["chi_next_sh_ratio"], 0.8, 1.2)
        + _norm(proxy["new_high_20d_pct"], 0.02, 0.08)
    ) / 7.0

    proxy["composite_score"] = max(0.0, min(1.0, score))
    return proxy


def _norm(value: float, lo: float, hi: float) -> float:
    """Normalize value to [0, 1] given range [lo, hi]."""
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _simulated_northbound(date: str) -> float:
    """Simulate northbound inflow (5-day sum, in 亿元)."""
    seed = _date_seed(date)
    return (seed % 100) - 30


def _simulated_turnover(date: str) -> float:
    """Simulate market turnover MA change rate."""
    seed = _date_seed(date)
    return ((seed % 100) / 100.0) - 0.1


def _simulated_main_force(date: str) -> float:
    """Simulate main force net inflow (5-day, in 亿元)."""
    seed = _date_seed(date)
    return (seed % 80) - 20


def _simulated_margin(date: str) -> float:
    """Simulate margin balance change rate."""
    seed = _date_seed(date)
    return ((seed % 100) / 1000.0) - 0.02


def _simulated_advance_decline(date: str) -> float:
    """Simulate advance/decline ratio."""
    seed = _date_seed(date)
    return 0.5 + (seed % 100) / 100.0


def _simulated_chi_next_sh(date: str) -> float:
    """Simulate ChiNext / Shanghai composite strength ratio."""
    seed = _date_seed(date)
    return 0.9 + (seed % 30) / 100.0


def _simulated_new_high(date: str) -> float:
    """Simulate 20-day new high percentage."""
    seed = _date_seed(date)
    return ((seed % 100) / 100.0) * 0.1


def _date_seed(date: str) -> int:
    """Deterministic pseudo-random seed from date string."""
    h = 0
    for ch in date:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h
