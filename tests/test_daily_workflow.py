"""Tests for daily workflow."""

import pytest

from zhixing_quant.data.store import BarStore
from zhixing_quant.executor.daily_workflow import run_daily_workflow


def _seed_oamv(store):
    # 40 根平盘线，收盘全 100 → 均线 100，ratio=0，score=0.5 → NEUTRAL
    rows = [(20240401 + i, 1.0, 1.0, 1.0, 100.0, 1e10, 2e11)
            for i in range(40)]
    store.upsert_oamv(rows)


@pytest.fixture
def oamv_store(tmp_path, monkeypatch):
    from zhixing_quant.data import tdx_loader

    store = BarStore(tmp_path / "wf.db")
    _seed_oamv(store)
    monkeypatch.setattr(tdx_loader, "get_store", lambda: store)
    yield store
    store.close()


def test_run_daily_workflow_returns_plan(oamv_store):
    plan = run_daily_workflow("2024-06-15", {})
    assert plan.date == "2024-06-15"
    assert plan.regime in ("BULL", "BEAR", "NEUTRAL")
    assert plan.mode in ("BULL", "BEAR", "NEUTRAL")
    assert isinstance(plan.actions, list)


def test_workflow_actions_contain_scan(oamv_store):
    plan = run_daily_workflow("2024-06-15", {})
    if plan.mode != "BEAR":
        assert any(a.startswith("SCAN:") for a in plan.actions)


def test_workflow_flat_oamv_is_neutral(oamv_store):
    plan = run_daily_workflow("2024-06-15", {})
    assert plan.regime == "NEUTRAL"
    assert plan.mode == "NEUTRAL"


def test_workflow_crash_day_is_bear(tmp_path, monkeypatch):
    from zhixing_quant.data import tdx_loader

    store = BarStore(tmp_path / "bear.db")
    rows = [(20240401 + i, 1.0, 1.0, 1.0, c, 1e10, 2e11)
            for i, c in enumerate([100.0] * 39 + [97.0])]   # 最后一天 -3%
    store.upsert_oamv(rows)
    monkeypatch.setattr(tdx_loader, "get_store", lambda: store)

    plan = run_daily_workflow("2024-06-15", {})
    assert plan.regime == "BEAR"
    assert plan.actions == []                # 空头区间禁止开仓
    store.close()
