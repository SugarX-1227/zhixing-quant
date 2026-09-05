"""Tests for defense engine."""

import pandas as pd

from zhixing_quant.portfolio.defense import DefenseEngine


def _base_row():
    return {
        "close": 105.0,
        "high": 107.0,
        "low": 103.0,
        "open": 104.0,
        "yellow_line": 101.0,
        "white_line": 102.0,
        "sig_s1": False,
        "sig_s2": False,
        "sig_s3": False,
        "sig_distribution": False,
        "sig_distribution_type": "none",
        "false_death_cross": False,
        "dist_stagnation": False,
    }


def test_defense_returns_none_when_safe():
    engine = DefenseEngine()
    df = pd.DataFrame([_base_row()])
    result = engine.evaluate(df, 0, {"take_profit": 200.0, "stop_loss": 50.0})
    assert result is None


def test_defense_triggers_stop_loss_p2():
    engine = DefenseEngine()
    row = _base_row()
    row["low"] = 49.0
    df = pd.DataFrame([row])
    result = engine.evaluate(df, 0, {"take_profit": 200.0, "stop_loss": 50.0})
    assert result is not None
    assert result.priority == 2
    assert result.reason == "stop_loss_hit"


def test_defense_triggers_take_profit_p1():
    engine = DefenseEngine()
    row = _base_row()
    row["high"] = 201.0
    df = pd.DataFrame([row])
    result = engine.evaluate(df, 0, {"take_profit": 200.0, "stop_loss": 50.0})
    assert result is not None
    assert result.priority == 1
    assert result.reason == "take_profit_hit"


def test_defense_triggers_s1_p3():
    engine = DefenseEngine()
    row = _base_row()
    row["sig_s1"] = True
    df = pd.DataFrame([row])
    result = engine.evaluate(df, 0, {"take_profit": 200.0, "stop_loss": 50.0})
    assert result is not None
    assert result.priority == 3


def test_defense_triggers_distribution_p4():
    engine = DefenseEngine()
    row = _base_row()
    row["sig_distribution"] = True
    row["sig_distribution_type"] = "sky_high"
    df = pd.DataFrame([row])
    result = engine.evaluate(df, 0, {"take_profit": 200.0, "stop_loss": 50.0})
    assert result is not None
    assert result.priority == 4


def test_defense_triggers_yellow_break_p7():
    engine = DefenseEngine()
    row = _base_row()
    row["close"] = 105.0
    row["white_line"] = 103.0  # close > white, but close < yellow
    row["yellow_line"] = 108.0
    df = pd.DataFrame([row])
    result = engine.evaluate(df, 0, {"take_profit": 200.0, "stop_loss": 50.0})
    assert result is not None
    assert result.priority == 7
    assert result.reason == "below_yellow_line"
