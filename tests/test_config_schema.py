"""配置校验测试。

本仓库栽在「配置改了不生效」上的次数比任何算法错误都多，所以每一种
历史上真实发生过、或新配置段里可能发生的静默失效，都要有一条测试。
"""

from __future__ import annotations

import pytest
import yaml

from zhixing_quant.config_schema import (DuplicateKeyError, StrictLoader,
                                         validate)


def _errs(cfg) -> str:
    return " ".join(validate(cfg).errors)


def _warns(cfg) -> str:
    return " ".join(validate(cfg).warnings)


# ---------------------------------------------------------------------------
# 重复键：历史上 timing / portfolio 各写两遍，前一份成了死配置
# ---------------------------------------------------------------------------

def test_duplicate_top_level_key_is_rejected():
    text = "timing:\n  a: 1\nportfolio:\n  x: 1\ntiming:\n  a: 2\n"
    with pytest.raises(DuplicateKeyError):
        yaml.load(text, Loader=StrictLoader)


def test_duplicate_nested_key_is_rejected():
    text = "exits:\n  b2:\n    break_yellow_line: true\n    break_yellow_line: false\n"
    with pytest.raises(DuplicateKeyError):
        yaml.load(text, Loader=StrictLoader)


def test_pyyaml_default_would_have_silently_swallowed_it():
    """对照组：说明为什么必须换严格 loader。"""
    text = "timing:\n  a: 1\ntiming:\n  a: 2\n"
    assert yaml.safe_load(text) == {"timing": {"a": 2}}


def test_strict_loader_accepts_normal_config():
    from zhixing_quant.config import DEFAULT_CONFIG_PATH

    with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = yaml.load(f, Loader=StrictLoader)
    assert isinstance(cfg, dict) and "exits" in cfg


# ---------------------------------------------------------------------------
# 枚举值写错 → 静默落回默认值
# ---------------------------------------------------------------------------

def test_wrong_case_stop_kind_is_rejected():
    """`ATR` 会静默落回 entry_low，你以为在测 ATR 止损其实不是。"""
    assert "stop.kind" in _errs({"exits": {"b2": {"stop": {"kind": "ATR"}}}})


def test_unknown_trailing_kind_is_rejected():
    assert "trailing.kind" in _errs(
        {"exits": {"b1": {"trailing": {"kind": "moving_average"}}}})


def test_unknown_take_profit_kind_is_rejected():
    assert "take_profit.kind" in _errs(
        {"exits": {"b2": {"take_profit": {"kind": "ladder"}}}})


def test_misspelled_preset_is_rejected():
    assert "by_strategy.b2" in _errs(
        {"factors": {"by_strategy": {"b2": "brekout"}}})


def test_valid_enums_pass():
    cfg = {"exits": {"b2": {"stop": {"kind": "atr"},
                            "trailing": {"kind": "white_line"},
                            "take_profit": {"kind": "none"}}},
           "factors": {"ranking": "pullback"}}
    assert validate(cfg, strict_factors=False).ok


# ---------------------------------------------------------------------------
# 键名打错 → 不生效也不报错
# ---------------------------------------------------------------------------

def test_misspelled_subkey_warns():
    assert "knid" in _warns({"exits": {"b2": {"stop": {"knid": "atr"}}}})


def test_misspelled_top_level_exit_key_warns():
    assert "brek_yellow_line" in _warns(
        {"exits": {"b2": {"brek_yellow_line": True}}})


def test_misspelled_entries_key_warns():
    assert "addon_percent" in _warns({"entries": {"b1": {"addon_percent": 0.05}}})


# ---------------------------------------------------------------------------
# 配了但永远不会触发的哑开关
# ---------------------------------------------------------------------------

def test_tiered_without_tiers_is_rejected():
    assert "永远不会触发" in _errs(
        {"exits": {"brick": {"take_profit": {"kind": "tiered"}}}})


def test_price_tiers_without_tiers_is_rejected():
    assert "永远不会触发" in _errs(
        {"exits": {"b1": {"take_profit": {"kind": "price_tiers"}}}})


def test_malformed_price_tier_is_rejected():
    assert "price_tiers" in _errs({"exits": {"b1": {"take_profit": {
        "kind": "price_tiers", "price_tiers": [{"gain": 0.08}]}}}})


def test_custom_ranking_without_weights_is_rejected():
    assert "weights" in _errs({"factors": {"ranking": "custom", "weights": {}}})


def test_defense_ladder_with_empty_rules_warns():
    assert "一级都不会触发" in _warns(
        {"exits": {"b2": {"defense_ladder": True, "defense_rules": []}}})


def test_addon_pct_without_max_addons_warns():
    assert "不会加仓" in _warns(
        {"entries": {"b1": {"addon_pct": 0.05, "max_addons": 0}}})


# ---------------------------------------------------------------------------
# 比例写成百分数
# ---------------------------------------------------------------------------

def test_portion_above_one_is_rejected():
    assert "0~1" in _errs({"exits": {"b1": {"white_line_break": 50}}})


def test_portion_of_half_is_fine():
    assert validate({"exits": {"b1": {"white_line_break": 0.5}}}).ok


def test_base_pct_above_one_is_rejected():
    assert "0~1" in _errs({"entries": {"b1": {"base_pct": 15}}})


def test_non_numeric_portion_is_rejected():
    assert "必须是数字" in _errs({"exits": {"b1": {"white_line_break": "half"}}})


# ---------------------------------------------------------------------------
# 引用完整性
# ---------------------------------------------------------------------------

def test_unknown_factor_in_weights_is_rejected():
    assert "mom_999" in _errs(
        {"factors": {"ranking": "custom", "weights": {"mom_999": 1.0}}})


def test_registered_factors_pass():
    assert validate({"factors": {"ranking": "custom",
                                 "weights": {"mom_20": 1.0}}}).ok


def test_unknown_strategy_section_warns():
    assert "没有注册" in _warns({"exits": {"b3": {"stop": {"kind": "pct"}}}})


def test_default_section_is_allowed():
    assert "没有注册" not in _warns({"exits": {"default": {"stop": {"kind": "pct"}}}})


# ---------------------------------------------------------------------------
# 随仓配置本身必须干净
# ---------------------------------------------------------------------------

def test_shipped_config_passes_validation():
    """随仓 settings.yaml 不能有任何错误，也不该有告警。"""
    from zhixing_quant.config import load_config

    result = validate(load_config(validate=False))
    assert result.ok, result.report()
    assert not result.warnings, result.report()


def test_load_config_raises_on_invalid(tmp_path):
    from zhixing_quant.config import load_config

    bad = tmp_path / "bad.yaml"
    bad.write_text("exits:\n  b2:\n    stop:\n      kind: ATR\n", encoding="utf-8")
    with pytest.raises(ValueError, match="配置校验未通过"):
        load_config(bad)


def test_load_config_can_skip_validation(tmp_path):
    from zhixing_quant.config import load_config

    bad = tmp_path / "bad.yaml"
    bad.write_text("exits:\n  b2:\n    stop:\n      kind: ATR\n", encoding="utf-8")
    assert load_config(bad, validate=False)["exits"]["b2"]["stop"]["kind"] == "ATR"


def test_warnings_are_exposed_for_the_ui(tmp_path):
    from zhixing_quant.config import config_warnings, load_config

    f = tmp_path / "warn.yaml"
    f.write_text("exits:\n  b2:\n    stop:\n      knid: atr\n", encoding="utf-8")
    load_config(f)
    assert any("knid" in w for w in config_warnings())
    load_config()          # 恢复，别污染后面的测试


def test_empty_config_is_not_a_crash():
    assert validate({}).ok


def test_non_dict_sections_are_reported():
    assert "必须是字典" in _errs({"exits": ["b2"]})
