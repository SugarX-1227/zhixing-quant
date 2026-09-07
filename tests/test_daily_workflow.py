"""Tests for daily workflow."""

import pytest

from zhixing_quant.executor.daily_workflow import run_daily_workflow


def test_run_daily_workflow_returns_plan():
    plan = run_daily_workflow("2024-06-15", {})
    assert plan.date == "2024-06-15"
    assert plan.regime in ("BULL", "BEAR", "NEUTRAL")
    assert plan.mode in ("BULL", "BEAR", "NEUTRAL")
    assert isinstance(plan.actions, list)


def test_workflow_actions_contain_scan():
    plan = run_daily_workflow("2024-06-15", {})
    if plan.mode != "BEAR":
        assert any(a.startswith("SCAN:") for a in plan.actions)
