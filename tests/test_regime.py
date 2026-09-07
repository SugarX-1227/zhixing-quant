"""Tests for regime state machine."""

import pytest

from zhixing_quant.timing.regime import RegimeStateMachine


def test_regime_starts_neutral():
    sm = RegimeStateMachine()
    assert sm.current_regime == "NEUTRAL"
    assert sm.days_in_regime == 0


def test_neutral_to_bull_on_high_score():
    sm = RegimeStateMachine()
    state = sm.update(0.70)
    assert state["current_regime"] == "BULL"
    assert state["days_in_regime"] == 1


def test_neutral_to_bear_on_low_score():
    sm = RegimeStateMachine()
    state = sm.update(0.20)
    assert state["current_regime"] == "BEAR"
    assert state["days_in_regime"] == 1


def test_bull_to_neutral_on_drop():
    sm = RegimeStateMachine()
    sm.update(0.70)
    sm.update(0.75)
    state = sm.update(0.55)
    assert state["current_regime"] == "NEUTRAL"
    assert state["days_in_regime"] == 0


def test_bear_to_neutral_on_rise():
    sm = RegimeStateMachine()
    sm.update(0.20)
    sm.update(0.25)
    state = sm.update(0.45)
    assert state["current_regime"] == "NEUTRAL"


def test_bull_strength_increases():
    sm = RegimeStateMachine()
    sm.update(0.70)
    s1 = sm.update(0.72)
    s2 = sm.update(0.75)
    assert s2["regime_strength"] > s1["regime_strength"]


def test_reset_returns_neutral():
    sm = RegimeStateMachine()
    sm.update(0.70)
    sm.update(0.75)
    sm.reset()
    assert sm.current_regime == "NEUTRAL"
    assert sm.days_in_regime == 0


def test_custom_thresholds():
    sm = RegimeStateMachine(bull_threshold=0.80, bear_threshold=0.20)
    state = sm.update(0.75)
    assert state["current_regime"] == "NEUTRAL"
    state = sm.update(0.85)
    assert state["current_regime"] == "BULL"
