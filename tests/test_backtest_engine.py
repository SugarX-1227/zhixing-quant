"""回测引擎测试。重点覆盖旧版存在的几个 bug。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.backtest.engine import BacktestEngine

CFG = {
    "backtest": {
        "initial_capital": 100000,
        "commission": 0.00025,
        "commission_min": 5,
        "stamp_tax": 0.001,
        "transfer_fee": 0.00001,
        "slippage": 0.0,          # 关掉滑点方便断言
        "max_positions": 1,
        "max_holding_days": 5,
        "max_entries_per_day": 1,
        "limit_up_pct": 0.10,
    },
    "execution": {"abandon_gap_up": 0.99},   # 关掉高开放弃
}


def _frame(rows: list, sig_idx: list = ()) -> pd.DataFrame:
    """rows: [(open, high, low, close), ...]"""
    idx = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=len(rows), freq="B"), name="date")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["amount"] = 1e9
    df["vol"] = 1e6
    df["sig"] = False
    for i in sig_idx:
        df.iloc[i, df.columns.get_loc("sig")] = True
    df["stop_loss"] = df["low"] * 0.9
    return df


def test_engine_runs_without_crashing():
    """旧版没 import pandas，run() 必 NameError。这条守住这个回归。"""
    df = _frame([(10, 10.5, 9.5, 10)] * 20, sig_idx=[2])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    assert isinstance(res.equity_curve, pd.Series)
    assert len(res.equity_curve) == 20


def test_no_signal_means_flat_equity():
    df = _frame([(10, 10.5, 9.5, 10)] * 10)
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    assert res.metrics["total_trades"] == 0
    assert np.allclose(res.equity_curve.to_numpy(), 100000.0)


def test_entry_happens_next_day_open_not_signal_day_close():
    """信号在 T 日收盘产生，成交必须在 T+1 开盘，否则是未来函数。"""
    rows = [(10, 10.1, 9.9, 10)] * 3 + [(10.5, 10.6, 10.4, 10.5)] * 7
    df = _frame(rows, sig_idx=[2])          # 第 2 天（索引 2）收盘出信号
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")

    held = [d for d in res.daily_positions if d["positions"]]
    assert held, "T+1 应已建仓"
    # 建仓日必须是信号日的下一天，不能是信号日当天
    assert held[0]["date"] == df.index[3].strftime("%Y-%m-%d")
    assert res.daily_positions[2]["positions"] == []


def test_equity_accounts_for_principal_not_just_fees():
    """旧版买入只扣手续费不扣本金，资金曲线会凭空膨胀。"""
    rows = [(10, 10, 10, 10)] * 2 + [(10, 10, 10, 10)] * 8
    df = _frame(rows, sig_idx=[0])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    # 价格全程不动，扣掉手续费后权益必须略低于初始资金，绝不能高于
    assert res.equity_curve.iloc[-1] <= 100000.0
    assert res.equity_curve.iloc[-1] > 99000.0


def test_t_plus_1_blocks_same_day_sell():
    """当日买入当日不可卖，即使当天就跌破止损。"""
    rows = [(10, 10, 10, 10), (10, 10, 10, 10), (10, 10, 1, 1)] + [(1, 1, 1, 1)] * 5
    df = _frame(rows, sig_idx=[1])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    if res.trades:
        t = res.trades[0]
        assert t.exit_date > t.entry_date


def test_limit_up_open_blocks_entry():
    """一字涨停买不进。"""
    rows = [(10, 10, 10, 10), (11, 11, 11, 11)] + [(11, 11, 11, 11)] * 5
    df = _frame(rows, sig_idx=[0])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    assert res.daily_positions[1]["positions"] == []


def test_stop_loss_triggers():
    rows = [(10, 10, 10, 10)] * 2 + [(10, 10, 8, 8)] + [(8, 8, 8, 8)] * 5
    df = _frame(rows, sig_idx=[0])
    df["stop_loss"] = 9.0
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig", stop_col="stop_loss")
    assert any(t.exit_reason == "止损" for t in res.trades)


def test_take_profit_triggers():
    rows = [(10, 10, 10, 10)] * 2 + [(10, 13, 10, 13)] + [(13, 13, 13, 13)] * 5
    df = _frame(rows, sig_idx=[0])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig", take_profit_pct=0.15)
    assert any(t.exit_reason == "止盈" for t in res.trades)


def test_max_holding_days_forces_exit():
    df = _frame([(10, 10, 10, 10)] * 20, sig_idx=[0])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    assert any("持有满" in t.exit_reason for t in res.trades)


def test_max_positions_respected():
    cfg = {**CFG, "backtest": {**CFG["backtest"], "max_positions": 2, "max_entries_per_day": 5}}
    data = {
        code: _frame([(10, 10, 10, 10)] * 15, sig_idx=[0, 1, 2])
        for code in ("600000", "600001", "600002", "600003")
    }
    res = BacktestEngine(cfg).run(data, signal_col="sig")
    assert all(len(d["positions"]) <= 2 for d in res.daily_positions)


def test_shares_are_round_lots():
    df = _frame([(10.13, 10.5, 9.9, 10.2)] * 15, sig_idx=[0])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    for d in res.daily_positions:
        for p in d["positions"]:
            assert p["shares"] % 100 == 0


def test_empty_input_returns_empty_result():
    res = BacktestEngine(CFG).run({}, signal_col="sig")
    assert res.metrics["total_trades"] == 0
    assert res.trades_frame().empty


def test_missing_signal_column_is_skipped():
    df = _frame([(10, 10, 10, 10)] * 10)
    df = df.drop(columns=["sig"])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    assert res.metrics["total_trades"] == 0


# ---------------------------------------------------------------------------
# 活跃市值区间闸门：空头日禁止开仓，持仓当日开盘强制清仓
# ---------------------------------------------------------------------------

def _regime_map(df, bear_from_idx):
    return {ts.strftime("%Y-%m-%d"): ("BEAR" if i >= bear_from_idx else "NEUTRAL")
            for i, ts in enumerate(df.index)}


def test_bear_regime_liquidates_positions_at_open():
    # idx2 收盘出信号 → idx3 开盘买入 → idx4 起为空头，idx4 开盘强制清仓
    df = _frame([(10, 10.5, 9.5, 10)] * 8, sig_idx=[2])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  regime=_regime_map(df, 4))
    frame = res.trades_frame()
    assert len(frame) == 1
    t = frame.iloc[0]
    assert t["exit_reason"] == "空头区间清仓"
    assert t["entry_date"] < t["exit_date"]
    assert res.metrics["bear_regime_days"] == 4          # idx4..idx7
    assert res.metrics["bear_forced_exits"] == 1


def test_bear_regime_blocks_pending_entry():
    # idx2 收盘出信号，但 idx3 开盘已是空头 → 买单作废，整场空仓
    df = _frame([(10, 10.5, 9.5, 10)] * 8, sig_idx=[2])
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  regime=_regime_map(df, 3))
    assert res.metrics["total_trades"] == 0
    assert res.equity_curve.nunique() == 1               # 资金曲线纹丝不动


def test_no_regime_map_behaves_as_before():
    df = _frame([(10, 10.5, 9.5, 10)] * 12, sig_idx=[2])   # 留足持有满5日后的平仓日
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig")
    assert len(res.trades) == 1                          # 不传 regime 时行为不变
