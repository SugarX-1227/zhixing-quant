"""Tests for strategy mode mapping."""

import pytest

from zhixing_quant.timing.strategy_mode import get_strategy_mode


def test_bull_mode_allows_all_signals():
    mode = get_strategy_mode("BULL", 0.8)
    assert mode["mode"] == "BULL"
    assert mode["allow_open"] is True
    assert mode["max_position_pct"] == 0.80
    assert "b1" in mode["allowed_signals"]
    assert "sb1" in mode["allowed_signals"]
    assert "b2" in mode["allowed_signals"]
    assert "brick" in mode["allowed_signals"]


def test_bear_mode_disallows_opens():
    mode = get_strategy_mode("BEAR", 0.8)
    assert mode["mode"] == "BEAR"
    assert mode["allow_open"] is False
    assert mode["max_position_pct"] == 0.0
    assert mode["allowed_signals"] == []


def test_neutral_mode_allows_core_signals():
    mode = get_strategy_mode("NEUTRAL", 0.5)
    assert mode["mode"] == "NEUTRAL"
    assert mode["allow_open"] is True
    assert mode["max_position_pct"] == 0.50
    assert "b1" in mode["allowed_signals"]
    assert "sb1" in mode["allowed_signals"]
    assert "b2" in mode["allowed_signals"]
    assert "brick" not in mode["allowed_signals"]
    assert "b3" not in mode["allowed_signals"]
    assert "violent_k" not in mode["allowed_signals"]
