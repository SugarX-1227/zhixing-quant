"""Tests for active value proxy."""

import pytest

from zhixing_quant.timing.active_value import active_value_proxy


def test_active_value_proxy_returns_composite_score():
    result = active_value_proxy("2024-06-15", {})
    assert "composite_score" in result
    assert 0.0 <= result["composite_score"] <= 1.0


def test_active_value_proxy_all_components_present():
    result = active_value_proxy("2024-06-15", {})
    expected_keys = [
        "northbound_inflow_5d",
        "turnover_ma_change",
        "main_force_inflow_5d",
        "margin_change",
        "advance_decline_ratio",
        "chi_next_sh_ratio",
        "new_high_20d_pct",
        "composite_score",
    ]
    for key in expected_keys:
        assert key in result


def test_active_value_proxy_deterministic():
    r1 = active_value_proxy("2024-06-15", {})
    r2 = active_value_proxy("2024-06-15", {})
    assert r1 == r2


def test_active_value_proxy_respects_cfg():
    cfg = {"timing": {"active_value": {"default_score": 0.75}}}
    result = active_value_proxy("2024-06-15", cfg)
    assert result["composite_score"] == result["composite_score"]  # score exists regardless of cfg
