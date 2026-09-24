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


def test_chinext_limit_was_ten_percent_before_the_2020_reform():
    """创业板 2020-08-24 起才从 10% 放宽到 20%。

    一刀切按 20% 的话，2020-08 之前的创业板一字板会被判成「没涨停」，
    从而虚构出一批实盘根本买不进的成交。
    """
    eng = BacktestEngine(CFG)
    assert eng._limit_pct("300750", pd.Timestamp("2020-08-21")) == 0.10
    assert eng._limit_pct("300750", pd.Timestamp("2020-08-24")) == 0.20
    assert eng._limit_pct("301001", pd.Timestamp("2019-01-01")) == 0.10


def test_star_market_is_always_twenty_percent():
    """科创板 2019 年开市即 20%，没有这个时间点。"""
    eng = BacktestEngine(CFG)
    assert eng._limit_pct("688001", pd.Timestamp("2019-07-22")) == 0.20


def test_main_board_is_unaffected_by_the_reform_date():
    eng = BacktestEngine(CFG)
    for d in ("2019-01-01", "2026-01-01"):
        assert eng._limit_pct("600000", pd.Timestamp(d)) == 0.10


def test_limit_pct_without_a_date_assumes_current_rules():
    """不传日期时按现行规则，保持既有调用方的行为不变。"""
    assert BacktestEngine(CFG)._limit_pct("300750") == 0.20


# ---------------------------------------------------------------------------
# 核心仓：多头区间闲置现金持有指数 ETF，个股买单现金不够时先卖核心仓
# ---------------------------------------------------------------------------

def _core(n, start=100.0, step=0.01):
    """每天开盘到收盘涨 step、收盘到次日开盘不动的指数。"""
    idx = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=n, freq="B"), name="date")
    opens = start * (1 + step) ** np.arange(n)
    return pd.DataFrame({"open": opens, "close": opens * (1 + step)}, index=idx)


def _regimes(idx, bull_from, bear_from):
    return {ts.strftime("%Y-%m-%d"): ("BEAR" if i >= bear_from else
                                      "BULL" if i >= bull_from else "NEUTRAL")
            for i, ts in enumerate(idx)}


def test_timing_only_holds_core_in_bull_and_exits_in_bear():
    """区间是开盘可知的：BULL 当天开盘买进，BEAR 当天开盘卖出，其余日子不动。"""
    core = _core(10)
    reg = _regimes(core.index, bull_from=2, bear_from=6)
    res = BacktestEngine(CFG).run({}, regime=reg, core_prices=core)
    eq = res.equity_curve
    assert len(eq) == 10
    assert eq.iloc[0] == eq.iloc[1] == 100000.0            # 未定区间空仓
    assert eq.iloc[2] > 100000.0 * 1.009                   # 第一个多头日开盘买，赚当天开→收
    assert eq.iloc[5] > eq.iloc[4] > eq.iloc[3] > eq.iloc[2]
    assert eq.iloc[6:].nunique() == 1                      # 空头日开盘清仓后纹丝不动
    assert res.metrics["total_trades"] == 0                # 核心仓不计入个股成交
    assert 0.35 < res.metrics["avg_core_weight"] < 0.45    # 10 天里持有 4 天
    assert res.metrics["core_fees"] >= 10                  # 买卖各至少 5 元


def test_core_is_not_bought_outside_bull():
    core = _core(8)
    reg = _regimes(core.index, bull_from=99, bear_from=99)  # 全程 NEUTRAL
    res = BacktestEngine(CFG).run({}, regime=reg, core_prices=core)
    assert res.equity_curve.nunique() == 1
    assert res.metrics["core_turnover"] == 0


def test_stock_buy_is_funded_by_selling_core():
    """现金全在核心仓里时，个股信号照样能买进，钱从核心仓里卖出来。"""
    cfg = {**CFG, "backtest": {**CFG["backtest"], "sizing": "pct", "position_pct": 0.305}}
    df = _frame([(10, 10.5, 9.5, 10)] * 10, sig_idx=[3])
    core = _core(10, step=0.0)
    reg = _regimes(df.index, bull_from=1, bear_from=99)
    res = BacktestEngine(cfg).run({"600000": df}, signal_col="sig", regime=reg,
                                  core_prices=core)
    snap = {d["date"]: d for d in res.daily_positions}
    day4 = df.index[4].strftime("%Y-%m-%d")                # idx3 收盘信号 → idx4 开盘买
    held = snap[day4]["positions"]
    assert held and held[0]["shares"] == 3000              # 30.5% × 权益（扣过核心仓费用）/ 10 元
    assert snap[day4]["cash"] < 0.02 * 100000              # 剩下的钱又回到核心仓
    # 第 0 天未定区间空仓，第 1 天起全程多头：除 1% 备付金外个股 + 核心仓满仓，
    # 10 天平均 ≈ 9/10 × 0.99
    assert res.metrics["avg_stock_weight"] + res.metrics["avg_core_weight"] > 0.88
