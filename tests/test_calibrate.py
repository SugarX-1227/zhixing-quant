"""参数校准测试。

这一层的全部价值在于**防过拟合**，所以测试重点不是「算得对不对」，
而是「该拦的有没有拦住」：样本内外必须按时间切、笔数不足必须排除出排名、
样本外衰减必须报警、条件参数造成的重复组合必须识别出来。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.backtest.calibrate import (BAD_DECAY, MIN_TRADES, OBJECTIVES,
                                              Period, _canonical_exit,
                                              _sweep_warnings, apply_params,
                                              config_signature, grid_combos,
                                              objective, plateau_score,
                                              rolling_windows, split_is_oos,
                                              walk_forward_summary)
from zhixing_quant.backtest.exits import spec_from_config


# ---------------------------------------------------------------------------
# 区间切分：必须按时间，不能随机
# ---------------------------------------------------------------------------

def test_is_oos_split_is_chronological_and_contiguous():
    """随机切会把未来信息混进训练集——同一只票相邻两天高度相关。"""
    a, b = split_is_oos("20240101", "20260101", 0.25)
    assert a.start == "20240101" and b.end == "20260101"
    assert a.end < b.start, "样本内必须整体早于样本外"
    assert pd.Timestamp(b.start) - pd.Timestamp(a.end) == pd.Timedelta(days=1)


def test_is_oos_fraction_is_respected():
    a, b = split_is_oos("20240101", "20260101", 0.25)
    total = (pd.Timestamp("20260101") - pd.Timestamp("20240101")).days
    oos = (pd.Timestamp(b.end) - pd.Timestamp(b.start)).days
    assert 0.2 < oos / total < 0.3


def test_is_oos_rejects_backwards_range():
    with pytest.raises(ValueError, match="不晚于"):
        split_is_oos("20260101", "20240101")


def test_is_oos_rejects_absurd_fraction():
    with pytest.raises(ValueError, match="oos_frac"):
        split_is_oos("20240101", "20260101", 0.95)


def test_rolling_windows_do_not_overlap_train_and_test():
    for train, test in rolling_windows("20200101", "20260101", 12, 3):
        assert train.end < test.start, "训练段必须早于测试段，否则就是偷看未来"


def test_rolling_windows_step_by_test_length():
    ws = rolling_windows("20200101", "20260101", 12, 3)
    assert len(ws) > 4
    starts = [pd.Timestamp(t.start) for t, _ in ws]
    gaps = {(b - a).days for a, b in zip(starts, starts[1:])}
    assert all(80 <= g <= 95 for g in gaps), "窗口应按测试段长度前进"


def test_rolling_windows_empty_when_range_too_short():
    assert rolling_windows("20240101", "20240601", 12, 3) == []


def test_period_describe_is_readable():
    assert "样本内" in Period("20240101", "20250101", "样本内 ").describe()


# ---------------------------------------------------------------------------
# 网格
# ---------------------------------------------------------------------------

def test_grid_combos_is_cartesian_product():
    combos = grid_combos({"a": [1, 2], "b": ["x", "y", "z"]})
    assert len(combos) == 6
    assert {"a": 2, "b": "z"} in combos


def test_empty_grid_yields_one_empty_combo():
    assert grid_combos({}) == [{}]


def test_apply_params_writes_nested_paths():
    out = apply_params({}, {"exits.b2.stop.pct": 0.07})
    assert out["exits"]["b2"]["stop"]["pct"] == 0.07


def test_apply_params_does_not_mutate_input():
    cfg = {"exits": {"b2": {"stop": {"pct": 0.05}}}}
    apply_params(cfg, {"exits.b2.stop.pct": 0.09})
    assert cfg["exits"]["b2"]["stop"]["pct"] == 0.05


def test_apply_params_keeps_siblings():
    cfg = {"exits": {"b2": {"stop": {"pct": 0.05, "kind": "pct"}}}}
    out = apply_params(cfg, {"exits.b2.stop.pct": 0.09})
    assert out["exits"]["b2"]["stop"]["kind"] == "pct"


# ---------------------------------------------------------------------------
# 条件参数去重
# ---------------------------------------------------------------------------

def test_inert_parameter_produces_the_same_signature():
    """kind=entry_low 时 stop.pct 根本不被读取，这两组不该各跑一次。"""
    from zhixing_quant.config import load_config

    cfg = load_config()
    a = apply_params(cfg, {"exits.b2.stop.kind": "entry_low",
                           "exits.b2.stop.pct": 0.04})
    b = apply_params(cfg, {"exits.b2.stop.kind": "entry_low",
                           "exits.b2.stop.pct": 0.10})
    assert config_signature(a, "b2") == config_signature(b, "b2")


def test_live_parameter_produces_different_signatures():
    from zhixing_quant.config import load_config

    cfg = load_config()
    a = apply_params(cfg, {"exits.b2.stop.kind": "pct",
                           "exits.b2.stop.pct": 0.04})
    b = apply_params(cfg, {"exits.b2.stop.kind": "pct",
                           "exits.b2.stop.pct": 0.10})
    assert config_signature(a, "b2") != config_signature(b, "b2")


def test_signature_separates_different_stop_kinds():
    from zhixing_quant.config import load_config

    cfg = load_config()
    a = apply_params(cfg, {"exits.b2.stop.kind": "entry_low"})
    b = apply_params(cfg, {"exits.b2.stop.kind": "atr"})
    assert config_signature(a, "b2") != config_signature(b, "b2")


def test_signature_notices_changes_outside_exits():
    from zhixing_quant.config import load_config

    cfg = load_config()
    b = apply_params(cfg, {"b2.j_threshold": 70})
    assert config_signature(cfg, "b2") != config_signature(b, "b2")


def test_canonical_exit_drops_inert_trailing_fields():
    from zhixing_quant.config import load_config

    cfg = load_config()
    a = spec_from_config(apply_params(cfg, {"exits.b1.trailing.kind": "none",
                                            "exits.b1.trailing.pct": 0.05}), "b1")
    b = spec_from_config(apply_params(cfg, {"exits.b1.trailing.kind": "none",
                                            "exits.b1.trailing.pct": 0.30}), "b1")
    assert _canonical_exit(a) == _canonical_exit(b)


# ---------------------------------------------------------------------------
# 目标函数：笔数不足必须出局
# ---------------------------------------------------------------------------

def test_objective_rejects_thin_samples():
    """20 笔的胜率说明不了任何问题，让它参与排名只会让噪声冒头。"""
    m = {"calmar": 5.0, "total_trades": MIN_TRADES - 1}
    assert objective(m) == float("-inf")


def test_objective_accepts_enough_trades():
    m = {"calmar": 1.2, "total_trades": MIN_TRADES}
    assert objective(m) == pytest.approx(1.2)


def test_objective_kinds_all_work():
    m = {"calmar": 1.0, "sharpe": 2.0, "total_return": 0.3,
         "max_drawdown": 0.1, "total_trades": 100}
    for kind in OBJECTIVES:
        assert np.isfinite(objective(m, kind))


def test_return_over_dd_handles_zero_drawdown():
    m = {"total_return": 0.3, "max_drawdown": 0.0, "total_trades": 100}
    assert objective(m, "return_over_dd") == 0.0


def test_default_objective_is_not_raw_return():
    """只看收益会选出「年化30%但回撤50%」这种实盘拿不住的参数。"""
    greedy = {"total_return": 1.0, "calmar": 0.1, "total_trades": 100}
    steady = {"total_return": 0.3, "calmar": 2.0, "total_trades": 100}
    assert objective(steady) > objective(greedy)


# ---------------------------------------------------------------------------
# 过拟合报警
# ---------------------------------------------------------------------------

def _table(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_warns_on_large_out_of_sample_decay():
    t = _table([{"内_总收益": 0.40, "外_总收益": 0.05, "笔数": 100}])
    text = " ".join(_sweep_warnings(t, Period("a", "b"), Period("c", "d")))
    assert "过拟合" in text


def test_warns_when_profitable_in_sample_loses_out_of_sample():
    t = _table([{"内_总收益": 0.30, "外_总收益": -0.10, "笔数": 100}])
    text = " ".join(_sweep_warnings(t, Period("a", "b"), Period("c", "d")))
    assert "不该上实盘" in text


def test_no_decay_warning_when_out_of_sample_holds_up():
    t = _table([{"内_总收益": 0.30, "外_总收益": 0.28, "笔数": 100}])
    text = " ".join(_sweep_warnings(t, Period("a", "b"), Period("c", "d")))
    assert "过拟合" not in text


def test_warns_when_in_and_out_of_sample_ranks_are_negatively_correlated():
    t = _table([
        {"内_总收益": 0.30, "外_总收益": -0.10, "笔数": 100},
        {"内_总收益": 0.20, "外_总收益": 0.05, "笔数": 100},
        {"内_总收益": 0.10, "外_总收益": 0.20, "笔数": 100},
    ])
    text = " ".join(_sweep_warnings(t, Period("a", "b"), Period("c", "d")))
    assert "负相关" in text


def test_warnings_always_restate_the_three_criteria():
    t = _table([{"内_总收益": 0.1, "外_总收益": 0.1, "笔数": 100}])
    text = " ".join(_sweep_warnings(t, Period("a", "b"), Period("c", "d")))
    assert "样本外不衰减" in text and "平坦" in text and "笔数" in text


# ---------------------------------------------------------------------------
# 参数平坦度
# ---------------------------------------------------------------------------

def test_flat_surface_scores_high():
    t = _table([{"p": i, "内_总收益": 0.20} for i in range(5)])
    assert plateau_score(t, "p") == pytest.approx(1.0)


def test_spiky_surface_scores_low():
    """孤立尖峰意味着换一段数据它就塌了。"""
    t = _table([{"p": 1, "内_总收益": 0.0}, {"p": 2, "内_总收益": 0.5},
                {"p": 3, "内_总收益": 0.0}, {"p": 4, "内_总收益": 0.5},
                {"p": 5, "内_总收益": 0.0}])
    assert plateau_score(t, "p") < 0.2


def test_gentle_slope_scores_between():
    t = _table([{"p": i, "内_总收益": 0.1 * i} for i in range(6)])
    assert 0.5 < plateau_score(t, "p") < 1.0


def test_plateau_score_needs_enough_points():
    assert plateau_score(_table([{"p": 1, "内_总收益": 0.1}]), "p") == 0.0


def test_plateau_score_missing_column_is_zero():
    assert plateau_score(_table([{"p": 1, "内_总收益": 0.1}]), "nope") == 0.0


# ---------------------------------------------------------------------------
# 滚动前进的结论
# ---------------------------------------------------------------------------

def test_walk_forward_summary_flags_mostly_losing_windows():
    wf = _table([
        {"训练段": "t1", "总收益": -0.05, "参数": "a=1"},
        {"训练段": "t2", "总收益": -0.03, "参数": "a=2"},
        {"训练段": "t3", "总收益": 0.01, "参数": "a=3"},
    ])
    assert "不要拿去实盘" in " ".join(walk_forward_summary(wf))


def test_walk_forward_summary_praises_a_stable_parameter():
    wf = _table([{"训练段": f"t{i}", "总收益": 0.05, "参数": "a=1"}
                 for i in range(4)])
    assert "确实稳定" in " ".join(walk_forward_summary(wf))


def test_walk_forward_summary_flags_parameter_churn():
    """每个窗口都换一套参数 = 选出来的是噪声。"""
    wf = _table([{"训练段": f"t{i}", "总收益": 0.05, "参数": f"a={i}"}
                 for i in range(5)])
    assert "噪声" in " ".join(walk_forward_summary(wf))


def test_walk_forward_summary_handles_empty():
    assert walk_forward_summary(pd.DataFrame()) == []
