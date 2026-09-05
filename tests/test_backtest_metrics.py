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
