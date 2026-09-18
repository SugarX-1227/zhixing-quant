"""Backtest performance metrics."""

from __future__ import annotations

from typing import Sequence


def compute_metrics(
    equity_curve: Sequence[float],
    trades: list,
    risk_free_rate: float = 0.03,
) -> dict:
    """Compute standard performance metrics.

    Args:
        equity_curve: Daily equity values.
        trades: List of trade dicts with 'return_pct' keys.
        risk_free_rate: Annual risk-free rate for Sharpe/Sortino.

    Returns:
        Dict with annualized_return, max_drawdown, sharpe, sortino,
        calmar, win_rate, profit_loss_ratio, total_trades.
    """
    if len(equity_curve) < 2:
        return _empty_metrics()

    equity = list(equity_curve)
    n = len(equity)
    years = n / 252.0
    total_return = (equity[-1] / equity[0]) - 1.0
    annual_return = (1.0 + total_return) ** (1.0 / max(years, 0.01)) - 1.0 if years > 0 else 0.0

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Daily returns
    daily_returns = [
        (equity[i] - equity[i - 1]) / equity[i - 1] if equity[i - 1] != 0 else 0.0
        for i in range(1, len(equity))
    ]
    if not daily_returns:
        return _empty_metrics()

    avg_daily = sum(daily_returns) / len(daily_returns)
    daily_rf = risk_free_rate / 252.0

    # Sharpe
    variance = sum((r - avg_daily) ** 2 for r in daily_returns) / len(daily_returns)
    std_daily = variance ** 0.5
    sharpe = ((avg_daily - daily_rf) / std_daily * (252 ** 0.5)) if std_daily > 0 else 0.0

    # Sortino：下行波动的基准应该是**目标收益**（这里取无风险利率），
    # 不是样本均值。原实现用 min(0, r - avg_daily)，等于「低于自己平均水平」
    # 都算下行——那是均值半方差，不是 Sortino。后果是策略越稳定（均值附近
    # 波动小）分母越小、Sortino 越虚高，恰好在最该保守的时候给了高分。
    downside = [min(0.0, r - daily_rf) ** 2 for r in daily_returns]
    down_std = (sum(downside) / len(downside)) ** 0.5 if downside else 0.0
    sortino = ((avg_daily - daily_rf) / down_std * (252 ** 0.5)) if down_std > 0 else 0.0

    # Calmar
    calmar = annual_return / max_dd if max_dd > 0 else 0.0

    # Trade stats
    total_trades = len(trades)
    wins = [t for t in trades if t.get("return_pct", 0) > 0]
    losses = [t for t in trades if t.get("return_pct", 0) <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    avg_win = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t["return_pct"] for t in losses) / len(losses)) if losses else 0.0
    # 没有亏损交易时盈亏比在数学上是无穷大。这里不返回 float("inf")，
    # 因为界面会把它直接塞进 f"{v:.2f}"；早先返回字符串 "inf" 更糟——
    # str 碰上 .2f 格式符直接 ValueError，整个回测页白屏。
    # 统一返回有限浮点，另用 profit_loss_ratio_is_inf 标明真实情况。
    pl_is_inf = avg_loss <= 0 and avg_win > 0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    return {
        "annualized_return": round(annual_return, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "win_rate": round(win_rate, 4),
        "profit_loss_ratio": round(pl_ratio, 4),
        "profit_loss_ratio_is_inf": pl_is_inf,
        "total_trades": total_trades,
    }


def _empty_metrics() -> dict:
    return {
        "annualized_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "calmar": 0.0,
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "profit_loss_ratio_is_inf": False,
        "total_trades": 0,
    }
