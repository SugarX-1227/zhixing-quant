"""持仓存储 / 仓位计算 / 每日主循环 测试。"""

from __future__ import annotations

import pytest

from zhixing_quant.portfolio.sizer import PositionSizer
from zhixing_quant.portfolio.store import PortfolioStore


# ---------------------------------------------------------------------------
# 持仓存储
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    s = PortfolioStore(tmp_path / "p.db")
    s.set_cash(500_000)
    yield s
    s.close()


def test_open_position_requires_stop_loss(store):
    """规格 01.2：build_trade_plan 不得返回没有止损位的计划。"""
    with pytest.raises(ValueError, match="缺少止损位"):
        store.open_position("600000", 100, 10.0, 0)
    with pytest.raises(ValueError, match="缺少止损位"):
        store.open_position("600000", 100, 10.0, None)


def test_stop_loss_must_be_below_entry(store):
    with pytest.raises(ValueError, match="不低于买入价"):
        store.open_position("600000", 100, 10.0, 11.0)


def test_shares_must_be_round_lot(store):
    with pytest.raises(ValueError, match="100 的整数倍"):
        store.open_position("600000", 150, 10.0, 9.5)


def test_adding_to_position_averages_entry_price(store):
    store.open_position("600000", 100, 10.0, 9.5)
    store.open_position("600000", 100, 12.0, 11.0)
    p = store.position("600000")
    assert p["shares"] == 200
    assert p["entry_price"] == pytest.approx(11.0)


def test_partial_close_marks_reduced(store):
    store.open_position("600000", 500, 10.0, 9.5)
    r = store.close_position("600000", 12.0, shares=200, reason="止盈")
    assert r["remaining"] == 300
    assert r["pnl"] == pytest.approx(400.0)
    assert store.position("600000")["state"] == "REDUCED"


def test_full_close_removes_position_and_logs_trade(store):
    store.open_position("600000", 100, 10.0, 9.5)
    store.close_position("600000", 11.0, reason="止盈")
    assert store.position("600000") is None
    assert len(store.trades()) == 1


def test_books_are_isolated(store):
    """规格 01.6 [LOCKED]：超短与波段必须分账户。"""
    store.open_position("600000", 100, 10.0, 9.5, book="swing")
    store.open_position("600000", 200, 10.0, 9.5, book="scalp")
    assert len(store.positions("swing")) == 1
    assert len(store.positions("scalp")) == 1
    assert store.position("600000", book="scalp")["shares"] == 200


def test_blacklist_roundtrip(store):
    store.blacklist_add("000002", "跌破黄线")
    assert "000002" in store.blacklisted()
    store.blacklist_clear("000002")
    assert "000002" not in store.blacklisted()


def test_equity_is_cash_plus_market_value(store):
    store.open_position("600000", 100, 10.0, 9.5)
    # 建仓不自动扣现金（现金由使用者维护），权益 = 现金 + 市值
    assert store.equity({"600000": 12.0}) == pytest.approx(500_000 + 1200)


def test_stats_on_empty_log(store):
    assert store.stats()["trades"] == 0


# ---------------------------------------------------------------------------
# 仓位计算
# ---------------------------------------------------------------------------

def test_risk_exposure_is_capped_regardless_of_price():
    """核心回归：修复前三种价位都撞择时上限，风险控制从未生效。"""
    s = PositionSizer(risk_pct=0.02, kelly_fraction=0.25)
    equity = 500_000
    budget = equity * 0.02 * 0.25          # 2,500 元
    for px, stop in [(10.0, 9.5), (50.0, 47.5), (100.0, 95.0)]:
        n = s.size(px, stop, equity, regime_max_pct=0.5, per_position_pct=0.20)
        assert n % 100 == 0
        assert s.risk_exposure(n, px, stop) <= budget + 1e-6


def test_bear_regime_blocks_sizing():
    s = PositionSizer()
    assert s.size(10.0, 9.5, 500_000, regime_max_pct=0.0) == 0


def test_invalid_stop_returns_zero():
    s = PositionSizer()
    assert s.size(10.0, 10.0, 500_000) == 0
    assert s.size(10.0, 11.0, 500_000) == 0
    assert s.size(0.0, 0.0, 500_000) == 0


def test_per_position_cap_binds():
    """单票上限比风险预算更紧时应该生效。"""
    s = PositionSizer(risk_pct=0.02, kelly_fraction=0.25)
    n = s.size(10.0, 9.99, 500_000, regime_max_pct=1.0, per_position_pct=0.05)
    assert n * 10.0 <= 500_000 * 0.05 + 1e-6


def test_expensive_stock_with_wide_stop_returns_zero():
    """一手风险就超预算时返回 0，而不是硬买。"""
    s = PositionSizer(risk_pct=0.02, kelly_fraction=0.25)
    assert s.size(1400.0, 1330.0, 500_000, regime_max_pct=0.5) == 0


# ---------------------------------------------------------------------------
# 每日主循环
# ---------------------------------------------------------------------------

def test_daily_result_actions_needed_filters():
    from zhixing_quant.executor.daily_workflow import DailyResult, HoldingReview

    r = DailyResult(date="2025-01-02", book="swing", regime="BULL",
                    regime_strength=0.6, regime_score=0.7, mode="BULL",
                    allow_open=True, max_total_pct=0.8)
    r.reviews = [
        HoldingReview("600000", "浦发", "brick", 100, 10, 11, 100, 0.1, 4, "LOW"),
        HoldingReview("000001", "平安", "brick", 100, 10, 9, -100, -0.1, 1, "HIGH",
                      action="SELL", action_reason="跌破黄线", action_priority=7),
    ]
    assert len(r.actions_needed) == 1
    assert r.actions_needed[0].code == "000001"


def test_sizing_stage_blocks_plan_without_stop():
    """没有止损的候选必须被拦截，不能静默下单。"""
    import pandas as pd

    from zhixing_quant.executor.daily_workflow import DailyResult, _stage_sizing

    r = DailyResult(date="2025-01-02", book="swing", regime="BULL",
                    regime_strength=0.6, regime_score=0.7, mode="BULL",
                    allow_open=True, max_total_pct=0.8)
    r.equity = 500_000
    r.candidates = pd.DataFrame([
        {"code": "600000", "name": "浦发银行", "close": 10.0, "stop_loss": 0.0},
        {"code": "000001", "name": "平安银行", "close": 10.0, "stop_loss": 9.5},
    ])
    _stage_sizing({"portfolio": {"risk_per_trade": 0.02, "kelly_fraction": 0.25}}, r)
    blocked = {p.code: p for p in r.plans if p.blocked}
    assert "600000" in blocked
    assert "止损" in blocked["600000"].blocked_reason
    assert "000001" not in blocked
