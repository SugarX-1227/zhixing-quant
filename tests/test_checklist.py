"""Tests for pre-order checklist."""

import pandas as pd

from zhixing_quant.executor.checklist import PreOrderChecklist


def _base_df():
    return pd.DataFrame([{
        "close": 105.0,
        "high": 107.0,
        "low": 103.0,
        "open": 104.0,
        "vol": 1000000,
        "dif": 1.0,
        "dea": 0.5,
        "bull_divergence": False,
        "bear_divergence": False,
        "false_golden_cross": False,
        "false_death_cross": False,
        "above_zero": True,
        "yellow_line": 101.0,
        "white_line": 102.0,
    }])


def _signal():
    from zhixing_quant.strategies.base import EntrySignal
    return EntrySignal(
        code="600519",
        date="2024-06-15",
        strategy="brick",
        price=105.0,
        stop_loss=102.0,
        take_profit=120.0,
        confidence=4,
        reason="test",
    )


def test_checklist_passes_when_clean():
    checklist = PreOrderChecklist()
    result = checklist.run(_signal(), _base_df(), {"equity": 100000.0})
    assert result.passed is True


def test_checklist_blocks_on_macd_veto():
    df = _base_df()
    df["above_zero"] = False
    checklist = PreOrderChecklist()
    result = checklist.run(_signal(), df, {"equity": 100000.0})
    assert result.passed is False
    assert "MACD veto triggered" in result.blockers
