"""Tests for position sizer."""

import pytest

from zhixing_quant.portfolio.sizer import PositionSizer


def test_sizer_returns_lot_size_multiple():
    sizer = PositionSizer()
    shares = sizer.size(entry_price=10.0, stop_loss=9.0, equity=100000.0)
    assert shares % 100 == 0


def test_sizer_returns_zero_when_no_risk():
    sizer = PositionSizer()
    shares = sizer.size(entry_price=10.0, stop_loss=10.0, equity=100000.0)
    assert shares == 0


def test_sizer_respects_regime_cap():
    sizer = PositionSizer()
    shares_cap = sizer.size(entry_price=10.0, stop_loss=9.0, equity=100000.0, regime_max_pct=0.1)
    shares_full = sizer.size(entry_price=10.0, stop_loss=9.0, equity=100000.0, regime_max_pct=1.0)
    assert shares_cap <= shares_full


# ---------------------------------------------------------------------------
# 单票上限 / 择时上限：回测与实盘必须读同一份
# ---------------------------------------------------------------------------

def test_per_position_cap_scales_with_account_size():
    from zhixing_quant.portfolio.sizer import per_position_cap

    cfg = {"portfolio": {"per_position_pct": {
        "small_capital": 0.50, "medium_expert": 0.20, "large": 0.07}}}
    assert per_position_cap(cfg, 50_000) == 0.50
    assert per_position_cap(cfg, 500_000) == 0.20
    assert per_position_cap(cfg, 5_000_000) == 0.07


def test_regime_cap_reads_config_not_hardcoded_values():
    from zhixing_quant.portfolio.sizer import regime_cap

    cfg = {"portfolio": {"bull_max_total": 0.9, "neutral_max_total": 0.4,
                         "bear_max_total": 0.0}}
    assert regime_cap(cfg, "BULL") == 0.9
    assert regime_cap(cfg, "NEUTRAL") == 0.4
    assert regime_cap(cfg, "BEAR") == 0.0
    assert regime_cap(cfg, "什么鬼") == 0.4, "未知区间按中性处理"


def test_daily_workflow_uses_the_shared_cap_helper():
    """两边各写一份就会漂移，这条锁住只有一份实现。"""
    import inspect

    from zhixing_quant.executor import daily_workflow

    src = inspect.getsource(daily_workflow)
    assert "_per_position_cap" not in src, "daily_workflow 又自己实现了一份"
    assert "per_position_cap" in src


def test_risk_sizing_respects_the_stop_distance():
    """止损越宽，同样的风险预算只能买越少股——equal 口径做不到这一点。"""
    from zhixing_quant.backtest.engine import BacktestEngine

    cfg = {"backtest": {"sizing": "risk", "initial_capital": 500000,
                        "max_positions": 5},
           "portfolio": {"risk_per_trade": 0.02, "kelly_fraction": 0.25,
                         "per_position_pct": {"medium_expert": 0.20}}}
    eng = BacktestEngine(cfg)
    tight = eng._entry_shares(500000, {}, 10.0, 9.5, 500000, 0.0, "NEUTRAL")
    wide = eng._entry_shares(500000, {}, 10.0, 8.0, 500000, 0.0, "NEUTRAL")
    assert tight > wide > 0, f"紧止损 {tight} 股 / 宽止损 {wide} 股，风险口径没生效"


def test_equal_sizing_ignores_the_stop_like_before():
    from zhixing_quant.backtest.engine import BacktestEngine

    eng = BacktestEngine({"backtest": {"sizing": "equal", "max_positions": 5}})
    a = eng._entry_shares(500000, {}, 10.0, 9.5, 500000, 0.0, "NEUTRAL")
    b = eng._entry_shares(500000, {}, 10.0, 8.0, 500000, 0.0, "NEUTRAL")
    assert a == b, "equal 口径本就与止损无关，改了说明动到了旧行为"


def test_regime_cap_blocks_entry_when_already_full():
    from zhixing_quant.backtest.engine import BacktestEngine

    cfg = {"backtest": {"sizing": "risk", "apply_regime_cap": True},
           "portfolio": {"risk_per_trade": 0.02, "kelly_fraction": 0.25,
                         "neutral_max_total": 0.5,
                         "per_position_pct": {"medium_expert": 0.20}}}
    eng = BacktestEngine(cfg)
    # 已持仓市值 = 权益的 50%，正好顶到中性上限
    assert eng._entry_shares(500000, {}, 10.0, 9.5, 1_000_000, 500_000,
                             "NEUTRAL") == 0
    assert eng._entry_shares(500000, {}, 10.0, 9.5, 1_000_000, 100_000,
                             "NEUTRAL") > 0
