"""出场规则消融 + 按实测 IC 生成因子权重。

这两样都是为了回答同一个问题：**规则/权重堆了一大堆，哪些真的有用？**
靠直觉判断不出来，所以要能测。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.backtest.ablation import (SWITCHES, _apply, _verdict,
                                             active_switches, summarize)
from zhixing_quant.backtest.exits import spec_from_config
from zhixing_quant.factors.evaluate import (T_SIGNIFICANT, direction_verdict,
                                            ic_summary, weights_as_yaml,
                                            weights_from_ic)


# ---------------------------------------------------------------------------
# 方向诊断
# ---------------------------------------------------------------------------

def test_direction_verdict_thresholds():
    assert direction_verdict(3.0) == "一致"
    assert direction_verdict(-3.0) == "⚠ 相反"
    assert direction_verdict(1.0) == "不显著"
    assert direction_verdict(-1.0) == "不显著"
    assert direction_verdict(float("nan")) == "不显著"


def test_direction_verdict_is_exactly_at_threshold():
    assert direction_verdict(T_SIGNIFICANT) == "一致"
    assert direction_verdict(-T_SIGNIFICANT) == "⚠ 相反"


def test_ic_summary_carries_the_direction_column():
    ic = pd.DataFrame({"good": [0.05] * 40, "bad": [-0.05] * 40},
                      index=pd.date_range("2024-01-01", periods=40, freq="B"))
    # 全常数序列 std=0 → ICIR=0，换成带噪声的
    rng = np.random.default_rng(0)
    ic = pd.DataFrame({"good": 0.06 + rng.normal(0, 0.05, 80),
                       "bad": -0.06 + rng.normal(0, 0.05, 80)},
                      index=pd.date_range("2024-01-01", periods=80, freq="B"))
    s = ic_summary(ic)
    assert "方向" in s.columns
    verdict = dict(zip(s["因子"], s["方向"]))
    assert verdict["good"] == "一致"
    assert verdict["bad"] == "⚠ 相反"


def test_empty_ic_summary_still_has_all_columns():
    """界面无条件读这些列，缺一个就是 KeyError。"""
    s = ic_summary(pd.DataFrame())
    for col in ("因子", "IC均值", "ICIR", "t值", "方向", "有效天数"):
        assert col in s.columns


# ---------------------------------------------------------------------------
# 按 IC 生成权重
# ---------------------------------------------------------------------------

def _summary(rows) -> pd.DataFrame:
    """rows: [(name, icir, t), ...]"""
    return pd.DataFrame([{"因子": n, "ICIR": i, "t值": t,
                          "方向": direction_verdict(t)} for n, i, t in rows])


def test_weights_drop_insignificant_factors():
    s = _summary([("a", 0.5, 8.0), ("b", 0.05, 0.9), ("c", 0.3, 4.0)])
    w = weights_from_ic(s)
    assert set(w) == {"a", "c"}, "不显著的因子必须被剔除"


def test_weights_are_proportional_to_icir_and_peak_at_one():
    s = _summary([("a", 0.6, 9.0), ("b", 0.3, 5.0)])
    w = weights_from_ic(s)
    assert w["a"] == pytest.approx(1.0)
    assert w["b"] == pytest.approx(0.5, abs=0.01)


def test_weights_flip_sign_for_reversed_factors():
    """实测方向相反的因子应该拿负权重＝反着用，而不是被当成正向。"""
    s = _summary([("good", 0.5, 8.0), ("reversed", -0.4, -6.0)])
    w = weights_from_ic(s, allow_flip=True)
    assert w["good"] > 0
    assert w["reversed"] < 0


def test_allow_flip_false_drops_reversed_factors():
    s = _summary([("good", 0.5, 8.0), ("reversed", -0.4, -6.0)])
    w = weights_from_ic(s, allow_flip=False)
    assert set(w) == {"good"}


def test_weights_respect_max_factors():
    s = _summary([(f"f{i}", 0.5 - i * 0.01, 9.0 - i * 0.1) for i in range(12)])
    assert len(weights_from_ic(s, max_factors=4)) == 4


def test_weights_empty_when_nothing_is_significant():
    """一个都不显著时必须返回空，而不是硬凑一个组合。"""
    s = _summary([("a", 0.05, 0.9), ("b", -0.03, -0.5)])
    assert weights_from_ic(s) == {}


def test_weights_empty_on_empty_summary():
    assert weights_from_ic(pd.DataFrame()) == {}


def test_weights_yaml_is_pasteable_and_parses():
    import yaml

    s = _summary([("amount_cv", 0.591, 11.0), ("mom_20", -0.307, -5.6)])
    text = weights_as_yaml(weights_from_ic(s), "b2")
    parsed = yaml.safe_load(text)
    assert parsed["factors"]["by_strategy"]["b2"] == "custom"
    assert parsed["factors"]["weights"]["amount_cv"] == pytest.approx(1.0)
    assert parsed["factors"]["weights"]["mom_20"] < 0


def test_weights_yaml_tells_you_to_stop_when_nothing_works():
    text = weights_as_yaml({}, "b2")
    assert "amount" in text and "没有因子" in text


def test_generated_weights_only_name_registered_factors():
    from zhixing_quant.factors.base import FACTOR_REGISTRY

    s = _summary([("amount_cv", 0.591, 11.0), ("atr_pct", 0.203, 3.8),
                  ("rev_5", 0.166, 3.1)])
    for name in weights_from_ic(s):
        assert name in FACTOR_REGISTRY


# ---------------------------------------------------------------------------
# 消融：开关定义
# ---------------------------------------------------------------------------

def test_every_switch_key_is_unique():
    keys = [sw.key for sw in SWITCHES]
    assert len(keys) == len(set(keys))


def test_active_switches_reflect_the_shipped_config():
    from zhixing_quant.config import load_config

    cfg = load_config()
    b2 = {sw.key for sw in active_switches(cfg, "b2")}
    # 随仓 b2 配了这些，消融必须都认得
    assert {"stop", "take_profit", "break_yellow_line", "no_progress",
            "defense_ladder"} <= b2


def test_inactive_rules_are_not_offered_for_ablation():
    """没启用的规则不该出现在消融列表里——跑一遍等于白跑。"""
    cfg = {"exits": {"b1": {"stop": {"kind": "none"},
                            "trailing": {"kind": "none"},
                            "take_profit": {"kind": "none"},
                            "time_stop": {"max_holding_days": 0}}}}
    keys = {sw.key for sw in active_switches(cfg, "b1")}
    assert "stop" not in keys and "trailing" not in keys
    assert "take_profit" not in keys and "max_holding" not in keys


def test_each_switch_actually_disables_its_rule():
    """逐条验证 off 里的路径真能把规则关掉——写错路径的话消融全是假的。"""
    from zhixing_quant.config import load_config

    cfg = load_config()
    for strategy in ("b1", "b2", "brick"):
        for sw in active_switches(cfg, strategy):
            off = spec_from_config(_apply(cfg, strategy, sw.off), strategy)
            assert not sw.active(off), \
                f"{strategy} 的「{sw.label}」关不掉，off 路径写错了：{sw.off}"


def test_apply_does_not_mutate_the_original_config():
    from zhixing_quant.config import load_config

    cfg = load_config()
    before = spec_from_config(cfg, "b2").break_yellow_line
    _apply(cfg, "b2", {"break_yellow_line": False})
    assert spec_from_config(cfg, "b2").break_yellow_line == before


def test_apply_leaves_other_rules_untouched():
    from zhixing_quant.config import load_config

    cfg = load_config()
    base = spec_from_config(cfg, "b2")
    off = spec_from_config(_apply(cfg, "b2", {"break_yellow_line": False}), "b2")
    assert off.stop.kind == base.stop.kind
    assert off.profit_to_loss == base.profit_to_loss
    assert off.time_stop.no_progress_days == base.time_stop.no_progress_days


def test_apply_creates_missing_nested_paths():
    out = _apply({}, "b2", {"time_stop.no_progress_days": 0})
    assert out["exits"]["b2"]["time_stop"]["no_progress_days"] == 0


# ---------------------------------------------------------------------------
# 消融：判定与摘要
# ---------------------------------------------------------------------------

def _base(ret=0.10, trades=100) -> dict:
    return {"总收益": ret, "笔数": trades}


def test_verdict_flags_a_rule_that_never_fires():
    v = _verdict({"总收益": 0.10, "笔数": 100}, _base(), only_one=False)
    assert "摆设" in v


def test_verdict_says_a_rule_loses_money_when_removing_it_helps():
    v = _verdict({"总收益": 0.15, "笔数": 90}, _base(), only_one=False)
    assert "在亏钱" in v


def test_verdict_says_a_rule_makes_money_when_removing_it_hurts():
    v = _verdict({"总收益": 0.02, "笔数": 120}, _base(), only_one=False)
    assert "在赚钱" in v


def test_verdict_ignores_sub_one_percent_noise():
    v = _verdict({"总收益": 0.105, "笔数": 101}, _base(), only_one=False)
    assert "影响很小" in v


def test_verdict_wording_flips_in_only_one_mode():
    v = _verdict({"总收益": 0.15, "笔数": 90}, _base(), only_one=True)
    assert "单独用" in v


def test_summarize_calls_out_hurtful_rules():
    df = pd.DataFrame([
        {"规则": "完整配置（基线）", "总收益": 0.10, "最大回撤": 0.2, "笔数": 100,
         "Δ总收益": 0.0, "判定": ""},
        {"规则": "关掉：低低走人", "总收益": 0.16, "最大回撤": 0.2, "笔数": 108,
         "Δ总收益": 0.06, "判定": "关掉后收益 +6.0% → 这条在亏钱"},
    ])
    text = " ".join(summarize(df))
    assert "低低走人" in text and "反而变好" in text


def test_summarize_warns_when_no_rule_matters():
    df = pd.DataFrame([
        {"规则": "完整配置（基线）", "总收益": 0.10, "最大回撤": 0.2, "笔数": 100,
         "Δ总收益": 0.0, "判定": ""},
        {"规则": "关掉：止盈", "总收益": 0.103, "最大回撤": 0.2, "笔数": 99,
         "Δ总收益": 0.003, "判定": "影响很小"},
    ])
    text = " ".join(summarize(df))
    assert "入场信号" in text, "所有规则都无关紧要时应该提示去看入场而不是继续调出场"


def test_summarize_always_warns_about_overfitting():
    df = pd.DataFrame([
        {"规则": "完整配置（基线）", "总收益": 0.10, "最大回撤": 0.2, "笔数": 100,
         "Δ总收益": 0.0, "判定": ""},
        {"规则": "关掉：止盈", "总收益": 0.16, "最大回撤": 0.2, "笔数": 99,
         "Δ总收益": 0.06, "判定": "关掉后收益 +6.0% → 这条在亏钱"},
    ])
    assert "样本外" in " ".join(summarize(df))


def test_summarize_handles_degenerate_input():
    assert summarize(pd.DataFrame()) == []
    assert summarize(None) == []


def test_backtest_run_carries_the_config_it_used():
    """消融必须在同一份配置上做，否则和界面上那张结果表不可比。"""
    import dataclasses

    from zhixing_quant.backtest.runner import BacktestRun

    names = {f.name for f in dataclasses.fields(BacktestRun)}
    assert "used_cfg" in names
