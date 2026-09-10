"""股票池时点正确性 / 回测执行器 / 参数覆盖 测试。"""

from __future__ import annotations

import pytest

from zhixing_quant.data.universe import (BOARDS, UniverseSpec, rebalance_dates,
                                         spec_from_config)
from zhixing_quant.ui import param_schema as PS


# ---------------------------------------------------------------------------
# 参数覆盖
# ---------------------------------------------------------------------------

def test_apply_overrides_does_not_mutate_original():
    cfg = {"b1": {"j_threshold": 13.0}, "backtest": {"slippage": 0.0015}}
    out = PS.apply_overrides(cfg, {"b1.j_threshold": 20.0})
    assert out["b1"]["j_threshold"] == 20.0
    assert cfg["b1"]["j_threshold"] == 13.0, "原配置被污染了"


def test_apply_overrides_creates_missing_path():
    out = PS.apply_overrides({}, {"a.b.c": 5})
    assert out["a"]["b"]["c"] == 5


def test_get_value_returns_default_for_missing():
    assert PS.get_value({}, "x.y", 7) == 7
    assert PS.get_value({"x": {"y": 1}}, "x.y", 7) == 1


def test_diff_from_default_only_reports_changes():
    changed = PS.diff_from_default({"b1.j_threshold": 13.0,
                                    "b1.chg_max": 8.0})
    assert "b1.j_threshold" not in changed      # 等于默认值
    assert changed["b1.chg_max"] == 8.0


def test_every_backtestable_strategy_has_params():
    from zhixing_quant.backtest.runner import BACKTESTABLE

    for name in BACKTESTABLE:
        assert PS.params_for(name), f"{name} 没有可调参数，回测页会是空的"


def test_locked_params_are_marked():
    """印花税是法定税率，不该当成可调参数。"""
    stamp = [p for p in PS.COST_PARAMS if p.key == "backtest.stamp_tax"][0]
    assert stamp.tag == "LOCKED"


def test_param_ranges_contain_defaults():
    groups = list(PS.STRATEGY_PARAMS.values()) + [
        PS.EXECUTION_PARAMS, PS.COST_PARAMS, PS.RISK_PARAMS]
    for group in groups:
        for p in group:
            if p.kind == "bool" or p.lo is None:
                continue
            assert p.lo <= p.default <= p.hi, f"{p.key} 默认值不在取值范围内"


# ---------------------------------------------------------------------------
# 股票池
# ---------------------------------------------------------------------------

def test_universe_spec_describe_is_readable():
    s = UniverseSpec(boards=("MAIN",), size=100, min_amount=2e8)
    text = s.describe()
    assert "沪深主板" in text and "取前100只" in text


def test_boards_cover_all_board_codes():
    """data/sync.py::_board_of 会产出这几种，界面必须都能显示。"""
    from zhixing_quant.data.sync import _board_of

    produced = {_board_of(c) for c in
                ["600000", "688001", "300750", "301001", "430047", "000001"]}
    assert produced <= set(BOARDS), f"有板块没有中文名：{produced - set(BOARDS)}"


def test_spec_from_config_respects_exclude_boards():
    cfg = {"universe": {"exclude_boards": ["CHINEXT"], "max_candidates": 50,
                        "min_daily_amount": 1e8}}
    spec = spec_from_config(cfg)
    assert "CHINEXT" not in spec.boards
    assert spec.size == 50


def test_rebalance_dates_quarterly():
    d = rebalance_dates("20240101", "20241231", months=3)
    assert d[0] == "20240101"
    assert len(d) == 4


def test_rebalance_dates_single_when_range_short():
    assert len(rebalance_dates("20240101", "20240120", months=3)) == 1


# ---------------------------------------------------------------------------
# 回测执行器
# ---------------------------------------------------------------------------

def test_unsupported_strategy_raises_with_explanation():
    from zhixing_quant.backtest.runner import run_backtest

    with pytest.raises(ValueError, match="暂不支持回测"):
        run_backtest({}, "dual_line", "20240101", "20241231")


def test_backtestable_set_matches_signal_columns():
    """BACKTESTABLE 里声明的信号列必须真的由对应流水线产出。"""
    from zhixing_quant.backtest.runner import BACKTESTABLE
    from zhixing_quant.indicators.pipeline import PIPELINES

    for name in BACKTESTABLE:
        assert name in PIPELINES, f"{name} 没有指标流水线"


def test_backtest_run_drawdown_is_non_positive():
    import pandas as pd

    from zhixing_quant.backtest.runner import BacktestRun

    eq = pd.Series([100.0, 120.0, 90.0, 110.0])
    run = BacktestRun(metrics={}, equity_curve=eq, trades=pd.DataFrame())
    assert (run.drawdown <= 1e-9).all()
    assert run.drawdown.min() == pytest.approx(-0.25)


def test_excess_return_none_without_benchmark():
    import pandas as pd

    from zhixing_quant.backtest.runner import BacktestRun

    run = BacktestRun(metrics={"total_return": 0.4},
                      equity_curve=pd.Series([1.0, 1.4]),
                      trades=pd.DataFrame())
    assert run.excess_return is None


def test_excess_return_subtracts_benchmark():
    import pandas as pd

    from zhixing_quant.backtest.runner import BacktestRun

    run = BacktestRun(metrics={"total_return": 0.40},
                      equity_curve=pd.Series([100.0, 140.0]),
                      trades=pd.DataFrame(),
                      benchmark=pd.Series([100.0, 130.0]))
    assert run.excess_return == pytest.approx(0.10)
