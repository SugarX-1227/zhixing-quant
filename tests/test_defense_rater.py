"""Tests for holding rater."""

import pandas as pd

from zhixing_quant.portfolio.rater import HoldingRater


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


def test_rater_gives_5_stars_when_healthy():
    rater = HoldingRater()
    df = pd.DataFrame([_base_row()])
    result = rater.rate(df, 0, {})
    assert result.stars == 5
    assert result.risk_level == "LOW"


def test_rater_deducts_for_sell_signal():
    rater = HoldingRater()
    row = _base_row()
    row["sig_s1"] = True
    df = pd.DataFrame([row])
    result = rater.rate(df, 0, {})
    assert result.stars <= 3
    assert "sell_signal" in result.reasons


def test_rater_deducts_for_distribution():
    rater = HoldingRater()
    row = _base_row()
    row["sig_distribution"] = True
    df = pd.DataFrame([row])
    result = rater.rate(df, 0, {})
    assert result.stars <= 2
    assert "distribution_detected" in result.reasons


def test_rater_deducts_for_below_yellow():
    rater = HoldingRater()
    row = _base_row()
    row["close"] = 100.0
    row["yellow_line"] = 105.0
    df = pd.DataFrame([row])
    result = rater.rate(df, 0, {})
    assert result.stars <= 3
    assert "below_yellow" in result.reasons


def test_rater_risk_label_boundaries():
    rater = HoldingRater()
    row = _base_row()
    df = pd.DataFrame([row])
    assert rater.rate(df, 0, {}).risk_level == "LOW"
    row["sig_s1"] = True
    assert rater.rate(pd.DataFrame([row]), 0, {}).risk_level in ("HIGH", "MEDIUM", "CRITICAL")
