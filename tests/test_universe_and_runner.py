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


def test_run_backtest_builds_universe_as_of_start_date(monkeypatch):
    """阶段 3 的立论点：池子必须按**回测开始日**建，不能用最新快照。

    以前这条只在 runner 的 docstring 里写着，没有任何测试保护。
    只要有人把 as_of 默认值改成 None 或 end，回测收益会被系统性抬高，
    而且抬多少无法估计——这种 bug 不会让任何测试变红。
    """
    from zhixing_quant.backtest import runner

    seen = {}

    def fake_build_universe(cfg, as_of=None, spec=None):
        seen["as_of"] = as_of
        raise ValueError("到此为止，只验建池入参")

    monkeypatch.setattr(runner, "build_universe", fake_build_universe)

    with pytest.raises(ValueError):
        runner.run_backtest({}, "b1", "20240102", "20260909")

    assert seen["as_of"] == "20240102", (
        f"建池基准日是 {seen['as_of']}，不是回测开始日。这是前视偏差。")


def test_check_universe_as_of_flags_look_ahead():
    from zhixing_quant.backtest.runner import check_universe_as_of

    assert check_universe_as_of("20240102", "20240102") == []
    assert check_universe_as_of("20230101", "20240102") == []
    assert check_universe_as_of("20260101", "20240102"), "晚于开始日必须告警"
    assert "前视偏差" in check_universe_as_of("20260101", "20240102")[0]


def test_benchmark_fallbacks_contain_no_individual_stocks():
    """候选链里出现 6 位裸代码就是地雷。

    本地库把上证指数存成 sh000001，而裸 000001 是平安银行
    （见 data/sync.py 的注释）。原实现的最后一个候选正是 "000001"，
    最坏情况会拿一只银行股当大盘基准算超额收益。
    """
    from zhixing_quant.backtest.runner import BENCHMARK_FALLBACKS

    for code in BENCHMARK_FALLBACKS:
        assert code[:2] in ("sh", "sz"), f"{code} 是裸代码，可能撞上个股"
        assert len(code) == 8, f"{code} 不是 市场+6位 格式"


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


# ---------------------------------------------------------------------------
# 悬空引用（这类 bug 不会让任何现有测试变红）
# ---------------------------------------------------------------------------

def test_scanner_min_bars_reads_a_key_that_exists():
    """三个 scanner 的 min_bars 是 lambda，只在真正扫描时才求值。

    删配置键时它们不会报错，pytest 也不会变红——直到你点了「扫描」。
    重构 config 时这条能立刻抓住。
    """
    from zhixing_quant.config import load_config
    from zhixing_quant.scanner.daily_b1 import SPEC as B1
    from zhixing_quant.scanner.daily_b2 import SPEC as B2
    from zhixing_quant.scanner.daily_brick import SPEC as BRICK

    cfg = load_config()
    for spec in (B1, B2, BRICK):
        n = spec.min_bars(cfg)
        assert n >= 114, f"{spec.key} 的 min_bars={n}，不够黄线的 MA114 预热"


def test_param_schema_keys_exist_in_config():
    """回测页的每个可调参数都必须对应 config 里真实存在的路径。

    否则滑块调了个寂寞：apply_overrides 会凭空创建一个没人读的键。
    """
    from zhixing_quant.config import load_config
    from zhixing_quant.ui import param_schema as PS

    cfg = load_config()
    groups = list(PS.STRATEGY_PARAMS.values()) + [
        PS.EXECUTION_PARAMS, PS.COST_PARAMS, PS.RISK_PARAMS]
    missing = []
    for group in groups:
        for p in group:
            node, *rest = p.key.split(".")
            cur = cfg.get(node)
            for part in rest:
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                missing.append(p.key)
    assert not missing, f"这些参数在 config 里不存在：{missing}"


def test_scanner_extra_fields_columns_are_produced():
    """scanner 的 extra_fields 直接 r["列名"] 取值，缺列就是 KeyError。"""
    import numpy as np
    import pandas as pd

    from zhixing_quant.config import load_config
    from zhixing_quant.scanner.daily_b1 import SPEC as B1
    from zhixing_quant.scanner.daily_b2 import SPEC as B2
    from zhixing_quant.scanner.daily_brick import SPEC as BRICK

    cfg = load_config()
    rng = np.random.default_rng(5)
    close = pd.Series(20 * np.cumprod(1 + rng.normal(0.001, 0.02, 200)),
                      index=pd.bdate_range("2024-01-02", periods=200))
    df = pd.DataFrame({"open": close, "high": close * 1.03, "low": close * 0.97,
                       "close": close, "vol": rng.integers(1e6, 5e6, 200).astype(float),
                       "amount": close * 3e6}, index=close.index)

    for spec in (B1, B2, BRICK):
        out = spec.add_indicators(df, cfg)
        row = out.iloc[-1]
        spec.extra_fields(row)          # 缺列会在这里 KeyError
        spec.reason(row, cfg)           # 文案里引用的列也一并验
