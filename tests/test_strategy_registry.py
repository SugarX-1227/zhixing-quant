"""Tests for strategy registry."""

import pytest

from zhixing_quant.strategies.registry import (
    STRATEGY_REGISTRY,
    get_strategy,
    list_strategies,
    register_strategy,
)


def test_registry_has_builtin_strategies():
    assert "brick" in STRATEGY_REGISTRY
    assert "b2" in STRATEGY_REGISTRY
    assert "single_needle" in STRATEGY_REGISTRY
    assert "dual_line" not in STRATEGY_REGISTRY   # 2026-09 使用者确认：双线就是 B1
    assert "yoga_pants" not in STRATEGY_REGISTRY  # 瑜伽裤是砖型图的另一叫法


def test_get_strategy_returns_instance():
    strategy = get_strategy("brick", {})
    assert strategy is not None
    assert strategy.name == "brick"


def test_get_strategy_unknown_returns_none():
    assert get_strategy("unknown", {}) is None


def test_list_strategies_returns_all():
    names = list_strategies()
    assert "brick" in names


def test_list_strategies_filter_by_regime():
    bull_names = list_strategies("BULL")
    assert "brick" in bull_names
