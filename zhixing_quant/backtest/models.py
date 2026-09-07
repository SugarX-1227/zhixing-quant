"""Slippage and limit-up/down models for backtest realism."""

from __future__ import annotations


def calc_slippage(price: float, side: str = "BUY", model: str = "pct") -> float:
    """Calculate slippage for an order.

    Args:
        price: Reference price.
        side: "BUY" or "SELL".
        model: "pct" (percentage) or "fixed" (absolute).

    Returns:
        Slippage amount (positive value to add/subtract from price).
    """
    if model == "fixed":
        return 0.01
    # Percentage model: 0.1% for liquid A-shares
    return price * 0.001


def calc_limit_price(prev_close: float, limit_pct: float = 0.10) -> tuple:
    """Calculate A-share limit-up/limit-down prices.

    Args:
        prev_close: Previous day closing price.
        limit_pct: Daily limit percentage (0.10 for main board, 0.20 for ChiNext/STAR).

    Returns:
        (limit_up_price, limit_down_price)
    """
    return (
        round(prev_close * (1 + limit_pct), 2),
        round(prev_close * (1 - limit_pct), 2),
    )


def is_limit_up(price: float, limit_up: float, tolerance: float = 0.001) -> bool:
    return price >= limit_up - tolerance


def is_limit_down(price: float, limit_down: float, tolerance: float = 0.001) -> bool:
    return price <= limit_down + tolerance
