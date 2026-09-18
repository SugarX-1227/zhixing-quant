"""Tests for backtest metrics."""

import pytest

from zhixing_quant.backtest.metrics import compute_metrics


def test_compute_metrics_basic():
    equity = [100000.0 + i * 100 for i in range(30)]
    trades = [{"return_pct": 0.05}, {"return_pct": -0.02}, {"return_pct": 0.08}]
    m = compute_metrics(equity, trades)
    assert m["total_trades"] == 3
    assert m["win_rate"] == pytest.approx(0.6667, abs=0.001)
    assert m["max_drawdown"] >= 0


def test_compute_metrics_handles_empty():
    m = compute_metrics([100000.0], [])
    assert m["total_trades"] == 0
    assert m["sharpe"] == 0.0


def test_sortino_uses_the_risk_free_target_not_the_sample_mean():
    """下行波动的基准是**目标收益**（无风险利率），不是样本均值。

    原实现用 min(0, r - avg_daily)，等于「低于自己平均水平」都算下行，
    那是均值半方差。后果是策略越稳定分母越小、Sortino 越虚高——
    恰好在最该保守的时候给了高分。
    """
    # 全部日收益都是 +0.1%，远高于日化无风险利率 → 没有任何下行 → Sortino 为 0
    equity = [100000 * (1.001 ** i) for i in range(60)]
    m = compute_metrics(equity, [{"return_pct": 0.05}])
    assert m["sortino"] == 0.0, (
        "每天都在赚、从未低于无风险收益，下行波动应为 0。"
        "拿样本均值当基准的话这里会算出一个有限的 Sortino。")


def test_sortino_is_finite_when_there_are_real_losses():
    equity = [100000, 101000, 99000, 100500, 98000, 101000, 99500, 102000]
    m = compute_metrics(equity, [{"return_pct": 0.02}])
    assert m["sortino"] != 0.0
