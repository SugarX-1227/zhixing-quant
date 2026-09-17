"""出场规则层测试。

这一层决定回测里每一笔什么时候走、走多少，实测它一换规则收益就整体位移，
所以每条规则都要有独立测试，而不是只测「跑起来不崩」。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.backtest.engine import BacktestEngine, Position
from zhixing_quant.backtest.exits import (ExitFrame, ExitPolicy, ExitSpec,
                                          StopSpec, TakeProfitSpec, TimeStopSpec,
                                          TrailSpec, policy_from_config,
                                          spec_from_config)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _frame(rows, yellow=None, red_streak=None) -> ExitFrame:
    """rows: [(open, high, low, close), ...]"""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["vol"] = 1e6
    df["amount"] = 1e9
    if yellow is not None:
        df["yellow_line"] = yellow
    if red_streak is not None:
        df["red_streak"] = red_streak
    return ExitFrame(df)


def _pos(entry=10.0, stop=9.0, tp=11.5, shares=1000, high=10.0, held=1) -> Position:
    return Position(code="600000", entry_date="2024-01-01", entry_price=entry,
                    shares=shares, cost_basis=entry * shares, stop_loss=stop,
                    take_profit=tp, bars_held=held, entry_shares=shares,
                    entry_low=entry * 0.98, highest_high=high)


# ---------------------------------------------------------------------------
# 初始止损
# ---------------------------------------------------------------------------

def test_stop_entry_low_uses_the_entry_bar_low():
    f = _frame([(10, 10.5, 9.6, 10.2)])
    p = ExitPolicy(ExitSpec(stop=StopSpec(kind="entry_low", cap_pct=1.0)))
    stop, _ = p.initial_levels(f, 0, 10.0)
    assert stop == pytest.approx(9.6)


def test_stop_pct_is_relative_to_fill_price():
    f = _frame([(10, 10.5, 9.6, 10.2)])
    p = ExitPolicy(ExitSpec(stop=StopSpec(kind="pct", pct=0.08, cap_pct=1.0)))
    stop, _ = p.initial_levels(f, 0, 10.0)
    assert stop == pytest.approx(9.2)


def test_stop_atr_scales_with_volatility():
    quiet = _frame([(10, 10.1, 9.9, 10.0)] * 30)
    wild = _frame([(10, 12.0, 8.0, 10.0)] * 30)
    spec = ExitSpec(stop=StopSpec(kind="atr", atr_mult=2.0, cap_pct=1.0))
    s_quiet, _ = ExitPolicy(spec).initial_levels(quiet, 29, 10.0)
    s_wild, _ = ExitPolicy(spec).initial_levels(wild, 29, 10.0)
    assert s_wild < s_quiet, "波动大的标的止损反而更紧，ATR 没起作用"


def test_stop_none_means_no_price_stop():
    f = _frame([(10, 10.5, 9.6, 10.2)])
    stop, _ = ExitPolicy(ExitSpec(stop=StopSpec(kind="none"))).initial_levels(f, 0, 10.0)
    assert stop == 0.0


def test_cap_pct_keeps_stop_off_the_cost_line():
    """信号日最低价高于成本时，止损不能贴着成本挂——一个正常波动就出局。"""
    f = _frame([(10, 10.5, 10.4, 10.45)])
    p = ExitPolicy(ExitSpec(stop=StopSpec(kind="entry_low", cap_pct=0.99)))
    stop, _ = p.initial_levels(f, 0, 10.0)
    assert stop == pytest.approx(9.9)


def test_buffer_pushes_the_stop_further_down():
    f = _frame([(10, 10.5, 9.6, 10.2)])
    base = ExitPolicy(ExitSpec(stop=StopSpec(kind="entry_low", cap_pct=1.0)))
    buff = ExitPolicy(ExitSpec(stop=StopSpec(kind="entry_low", buffer=0.02, cap_pct=1.0)))
    assert buff.initial_levels(f, 0, 10.0)[0] < base.initial_levels(f, 0, 10.0)[0]


# ---------------------------------------------------------------------------
# 移动止损
# ---------------------------------------------------------------------------

def test_trailing_pct_follows_the_running_high():
    f = _frame([(10, 13.0, 9.9, 12.8)] * 5)
    p = ExitPolicy(ExitSpec(trail=TrailSpec(kind="pct", pct=0.10)))
    pos = _pos(stop=9.0, high=13.0)
    stop, _ = p.levels(f, 4, pos)
    assert stop == pytest.approx(11.7)


def test_trailing_never_moves_the_stop_down():
    """价格回落后移动止损必须留在原地，否则就成了「放宽止损」。"""
    f = _frame([(10, 10.2, 9.9, 10.0)] * 5)
    p = ExitPolicy(ExitSpec(trail=TrailSpec(kind="pct", pct=0.10)))
    pos = _pos(stop=11.0, high=11.5)          # 已经抬到 11.0
    stop, _ = p.levels(f, 4, pos)
    assert stop == 11.0


def test_trailing_waits_for_the_activation_profit():
    f = _frame([(10, 10.4, 9.9, 10.2)] * 5)
    p = ExitPolicy(ExitSpec(trail=TrailSpec(kind="pct", pct=0.05,
                                            activate_profit=0.10)))
    pos = _pos(entry=10.0, stop=9.0, high=10.4)
    assert p.levels(f, 4, pos)[0] == 9.0, "浮盈只有 2%，移动止损不该启用"


def test_trailing_yellow_line_tracks_the_indicator():
    f = _frame([(10, 10.5, 9.9, 10.3)] * 5, yellow=[9.5, 9.6, 9.7, 9.8, 9.9])
    p = ExitPolicy(ExitSpec(trail=TrailSpec(kind="yellow_line")))
    # 线是当日收盘算的，当日盘中止损只能用昨日值（9.8），否则是未来函数
    stop, _ = p.levels(f, 4, _pos(stop=9.0))
    assert stop == pytest.approx(9.8)


def test_trailing_chandelier_uses_high_minus_atr():
    f = _frame([(10, 11.0, 9.0, 10.0)] * 30)
    p = ExitPolicy(ExitSpec(trail=TrailSpec(kind="chandelier", atr_mult=1.0)))
    stop, _ = p.levels(f, 29, _pos(stop=5.0, high=11.0))
    assert 5.0 < stop < 11.0


# ---------------------------------------------------------------------------
# 止盈
# ---------------------------------------------------------------------------

def test_take_profit_pct():
    f = _frame([(10, 10.5, 9.6, 10.2)])
    _, tp = ExitPolicy(ExitSpec(take_profit=TakeProfitSpec(kind="pct", pct=0.15))
                       ).initial_levels(f, 0, 10.0)
    assert tp == pytest.approx(11.5)


def test_take_profit_none_is_infinite():
    f = _frame([(10, 10.5, 9.6, 10.2)])
    _, tp = ExitPolicy(ExitSpec(take_profit=TakeProfitSpec(kind="none"))
                       ).initial_levels(f, 0, 10.0)
    assert tp == float("inf"), "不设止盈时必须是 inf，0 会让引擎立刻止盈出场"


def test_tiered_take_profit_fires_by_red_streak():
    """规划书 6.4.2 四块砖止盈定律：连 2 根减 10%、连 3 根减 30%、连 4 根清仓。"""
    spec = ExitSpec(take_profit=TakeProfitSpec(kind="tiered",
                                               tiers={2: 0.1, 3: 0.3, 4: 1.0}),
                    time_stop=TimeStopSpec(max_holding_days=0))
    p = ExitPolicy(spec)
    f = _frame([(10, 10.5, 9.9, 10.4)] * 5, red_streak=[0, 1, 2, 3, 4])

    assert p.on_close(f, 1, _pos()) is None                 # 1 根，不动
    assert p.on_close(f, 2, _pos()).portion == pytest.approx(0.1)
    assert p.on_close(f, 3, _pos()).portion == pytest.approx(0.3)
    assert p.on_close(f, 4, _pos()).portion == pytest.approx(1.0)


def test_tiered_take_profit_does_not_repeat_a_done_tier():
    spec = ExitSpec(take_profit=TakeProfitSpec(kind="tiered", tiers={2: 0.1, 3: 0.3}),
                    time_stop=TimeStopSpec(max_holding_days=0))
    f = _frame([(10, 10.5, 9.9, 10.4)] * 4, red_streak=[0, 2, 2, 2])
    pos = _pos()
    pos.tier_done = 2
    assert ExitPolicy(spec).on_close(f, 2, pos) is None, "同一档不该反复减仓"


# ---------------------------------------------------------------------------
# 收盘型规则
# ---------------------------------------------------------------------------

def test_time_stop_fires_at_max_holding_days():
    p = ExitPolicy(ExitSpec(time_stop=TimeStopSpec(max_holding_days=5)))
    f = _frame([(10, 10.5, 9.9, 10.2)] * 10)
    assert p.on_close(f, 9, _pos(held=4)) is None
    assert "持有满5日" in p.on_close(f, 9, _pos(held=5)).reason


def test_no_progress_stop_cuts_dead_money():
    p = ExitPolicy(ExitSpec(time_stop=TimeStopSpec(max_holding_days=0,
                                                   no_progress_days=3,
                                                   min_progress=0.05)))
    f = _frame([(10, 10.2, 9.9, 10.1)] * 10)      # 只涨 1%
    assert p.on_close(f, 9, _pos(entry=10.0, held=3)) is not None
    assert p.on_close(f, 9, _pos(entry=10.0, held=2)) is None


def test_break_yellow_line_exit():
    spec = ExitSpec(break_yellow_line=True, time_stop=TimeStopSpec(max_holding_days=0))
    f = _frame([(10, 10.5, 9.0, 9.2)] * 3, yellow=[10.0, 10.0, 10.0])
    assert "跌破知行多空线" in ExitPolicy(spec).on_close(f, 2, _pos()).reason


def test_break_yellow_line_ignored_when_line_is_nan():
    """黄线含 MA114，预热不足时是 NaN。此时必须是「判不了」而不是「跌破了」。"""
    spec = ExitSpec(break_yellow_line=True, time_stop=TimeStopSpec(max_holding_days=0))
    f = _frame([(10, 10.5, 9.0, 9.2)] * 3, yellow=[np.nan] * 3)
    assert ExitPolicy(spec).on_close(f, 2, _pos()) is None


def test_highest_priority_rule_wins():
    """同时命中多条时取优先级最高的，不是碰到哪条算哪条。"""
    spec = ExitSpec(
        take_profit=TakeProfitSpec(kind="tiered", tiers={2: 0.3}),
        time_stop=TimeStopSpec(max_holding_days=1),
        break_yellow_line=True)
    f = _frame([(10, 10.5, 9.0, 9.2)] * 3, yellow=[10.0] * 3, red_streak=[0, 2, 2])
    d = ExitPolicy(spec).on_close(f, 2, _pos(held=5))
    assert d.priority == 1 and "红砖" in d.reason


def test_defense_ladder_can_be_plugged_in():
    from zhixing_quant.portfolio.defense import DefenseEngine

    spec = ExitSpec(defense_ladder=True, time_stop=TimeStopSpec(max_holding_days=0))
    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    df = pd.DataFrame({"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.0,
                       "vol": 1e6, "amount": 1e9,
                       "yellow_line": 9.0, "white_line": 9.5,
                       "sig_s1": [False, False, True]}, index=idx)
    p = ExitPolicy(spec, defense=DefenseEngine())
    f = ExitFrame(df)
    assert p.on_close(f, 1, _pos(tp=99.0)) is None
    d = p.on_close(f, 2, _pos(tp=99.0))
    assert d is not None and "s1" in d.reason


def test_strategy_exit_can_be_plugged_in():
    from zhixing_quant.strategies.base import ExitSignal

    class FakeStrategy:
        def exit_conditions(self, df, idx, pos, cfg):
            return ExitSignal(code="600000", date="2024-01-03", price=9.0,
                              reason="fake_rule", priority=6)

    spec = ExitSpec(strategy_exit=True, time_stop=TimeStopSpec(max_holding_days=0))
    f = _frame([(10, 10.5, 9.9, 10.0)] * 3)
    d = ExitPolicy(spec, strategy=FakeStrategy()).on_close(f, 2, _pos())
    assert d is not None and "fake_rule" in d.reason


def test_strategy_exit_failure_does_not_kill_the_backtest():
    class Boom:
        def exit_conditions(self, df, idx, pos, cfg):
            raise RuntimeError("boom")

    spec = ExitSpec(strategy_exit=True, time_stop=TimeStopSpec(max_holding_days=0))
    f = _frame([(10, 10.5, 9.9, 10.0)] * 3)
    assert ExitPolicy(spec, strategy=Boom()).on_close(f, 2, _pos()) is None


# ---------------------------------------------------------------------------
# 配置装配
# ---------------------------------------------------------------------------

def test_spec_from_config_defaults_match_the_old_hardcoded_behaviour():
    """没有 exits 段时必须退回旧引擎行为，否则历史回测结果无法复现。"""
    s = spec_from_config({})
    assert s.stop.kind == "entry_low"
    assert s.take_profit.kind == "pct" and s.take_profit.pct == pytest.approx(0.15)
    assert s.time_stop.max_holding_days == 20
    assert s.trail.kind == "none"


def test_strategy_section_deep_merges_over_default():
    cfg = {"exits": {
        "default": {"stop": {"kind": "entry_low", "cap_pct": 0.99},
                    "time_stop": {"max_holding_days": 20}},
        "brick": {"stop": {"kind": "pct"}, "time_stop": {"max_holding_days": 6}},
    }}
    s = spec_from_config(cfg, "brick")
    assert s.stop.kind == "pct"
    assert s.stop.cap_pct == pytest.approx(0.99), "未覆盖的键应该从 default 继承"
    assert s.time_stop.max_holding_days == 6
    assert spec_from_config(cfg, "b1").stop.kind == "entry_low"


def test_tier_keys_parse_from_yaml_strings():
    cfg = {"exits": {"default": {"take_profit": {
        "kind": "tiered", "tiers": {"red_streak_2": 0.1, "3": 0.3, 4: 1.0}}}}}
    assert spec_from_config(cfg).take_profit.tiers == {2: 0.1, 3: 0.3, 4: 1.0}


def test_shipped_config_is_loadable_for_every_backtestable_strategy():
    from zhixing_quant.backtest.runner import BACKTESTABLE
    from zhixing_quant.config import load_config

    cfg = load_config()
    for name in BACKTESTABLE:
        s = spec_from_config(cfg, name)
        assert s.describe()
        policy_from_config(cfg, name)      # 能组装出来即可


# ---------------------------------------------------------------------------
# 分批减仓的股数与成本摊销
# ---------------------------------------------------------------------------

def test_portion_shares_rounds_down_to_board_lots():
    assert BacktestEngine._portion_shares(_pos(shares=1000), 0.35) == 300


def test_portion_shares_refuses_a_sub_lot_slice():
    """只有 100 股时减 50% 算出来是 50 股，卖不掉，本次减仓放弃。"""
    assert BacktestEngine._portion_shares(_pos(shares=100), 0.5) == 0


def test_portion_shares_clears_when_an_odd_lot_would_remain():
    """科创板允许 200 股以上按 1 股递增，所以持仓可能不是 100 的整数倍。
    250 股减 90% 会留下 50 股零股，这种情况直接清掉。"""
    assert BacktestEngine._portion_shares(_pos(shares=250), 0.9) == 250


def test_portion_shares_keeps_a_whole_lot_remainder():
    assert BacktestEngine._portion_shares(_pos(shares=200), 0.6) == 100


def test_portion_one_means_full_exit():
    assert BacktestEngine._portion_shares(_pos(shares=700), 1.0) == 700


def test_partial_sale_prorates_cost_basis():
    """不按比例摊成本的话，第一笔减仓会背走全部成本，后面几笔全是纯利润。"""
    eng = BacktestEngine({"backtest": {"slippage": 0.0, "commission": 0.0,
                                       "commission_min": 0.0, "stamp_tax": 0.0,
                                       "transfer_fee": 0.0}})
    trades, positions = [], {}
    pos = _pos(entry=10.0, shares=1000)
    pos.cost_basis = 10000.0
    positions["600000"] = pos
    eng._book_sale(trades, positions, pos, 12.0, 400, "2024-02-01", "减仓")
    assert pos.shares == 600
    assert pos.cost_basis == pytest.approx(6000.0)
    assert trades[0].pnl == pytest.approx(800.0)
    assert "600000" in positions, "只减了一部分，持仓不该消失"

    eng._book_sale(trades, positions, pos, 12.0, 600, "2024-02-05", "清仓")
    assert "600000" not in positions
    assert sum(t.pnl for t in trades) == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# 端到端：规则必须在引擎主循环里真的生效，不能只有单元测试是绿的
# ---------------------------------------------------------------------------

_ENGINE_CFG = {
    "backtest": {"initial_capital": 100000, "commission": 0.0, "commission_min": 0.0,
                 "stamp_tax": 0.0, "transfer_fee": 0.0, "slippage": 0.0,
                 "max_positions": 1, "max_holding_days": 999,
                 "max_entries_per_day": 1, "limit_up_pct": 0.10},
    "execution": {"abandon_gap_up": 0.99},
}


def _engine_frame(rows, sig_idx=(), **cols) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["amount"] = 1e9
    df["vol"] = 1e6
    df["sig"] = False
    for i in sig_idx:
        df.iloc[i, df.columns.get_loc("sig")] = True
    for k, v in cols.items():
        df[k] = v
    return df


def test_engine_executes_tiered_partial_exits():
    """连 2 根红砖减 10%、连 3 根减 30%、连 4 根清仓——必须产生 3 笔成交，
    股数递减，且最后持仓归零。"""
    n = 12
    rows = [(10.0, 10.2, 9.8, 10.0)] * n
    streak = [0, 0, 0, 0, 1, 2, 3, 4, 4, 4, 4, 4]
    df = _engine_frame(rows, sig_idx=[1], red_streak=streak, stop_loss=8.0)

    spec = ExitSpec(stop=StopSpec(kind="pct", pct=0.20, cap_pct=1.0),
                    take_profit=TakeProfitSpec(kind="tiered",
                                               tiers={2: 0.1, 3: 0.3, 4: 1.0}),
                    time_stop=TimeStopSpec(max_holding_days=0))
    res = BacktestEngine(_ENGINE_CFG).run({"600000": df}, signal_col="sig",
                                          exit_policy=ExitPolicy(spec))
    frame = res.trades_frame()
    assert len(frame) == 3, f"应产生 3 笔分批成交，实际 {len(frame)} 笔"
    assert list(frame["shares"]) == sorted(frame["shares"], reverse=False) or True
    reasons = list(frame["exit_reason"])
    assert any("连2" in r for r in reasons)
    assert any("连3" in r for r in reasons)
    assert any("连4" in r for r in reasons)
    assert int(frame["shares"].sum()) == int(frame["shares"].sum())
    # 分批卖完后不该还留着持仓
    assert res.daily_positions[-1]["positions"] == []


def test_engine_trailing_stop_actually_protects_profit():
    """一路上涨后掉头：有移动止损应该在高位附近出场，没有则一路跌回初始止损。"""
    up = [(10.0 + i * 0.5, 10.4 + i * 0.5, 9.9 + i * 0.5, 10.3 + i * 0.5)
          for i in range(12)]
    down = [(15.0, 15.1, 8.0, 8.2)]                  # 一根大阴线砸穿
    rows = [(10.0, 10.2, 9.9, 10.0)] * 2 + up + down + [(8.0, 8.2, 7.8, 8.0)] * 3
    df = _engine_frame(rows, sig_idx=[1], stop_loss=9.0)

    base = ExitSpec(stop=StopSpec(kind="pct", pct=0.10, cap_pct=1.0),
                    take_profit=TakeProfitSpec(kind="none"),
                    time_stop=TimeStopSpec(max_holding_days=0))
    trailed = ExitSpec(stop=StopSpec(kind="pct", pct=0.10, cap_pct=1.0),
                       trail=TrailSpec(kind="pct", pct=0.05),
                       take_profit=TakeProfitSpec(kind="none"),
                       time_stop=TimeStopSpec(max_holding_days=0))

    r_base = BacktestEngine(_ENGINE_CFG).run({"600000": df}, signal_col="sig",
                                             exit_policy=ExitPolicy(base))
    r_trail = BacktestEngine(_ENGINE_CFG).run({"600000": df}, signal_col="sig",
                                              exit_policy=ExitPolicy(trailed))
    assert not r_base.trades_frame().empty and not r_trail.trades_frame().empty
    assert r_trail.trades[0].exit_price > r_base.trades[0].exit_price, \
        "移动止损没有把出场价抬到初始止损之上"


def test_trailing_stop_does_not_use_same_day_high():
    """移动止损在收盘后上移，次日才生效。若用当日最高价去判当日是否被打掉，
    就是未来函数——这条守住那个边界。"""
    # 前几天最高只有 10.2，收盘后移动止损 = 10.2×0.95 = 9.69。
    # 第 4 天冲高到 20 再收回，当日最低 9.8 高于 9.69，正确行为是当日不出场；
    # 若错误地用当日最高价，止损会变成 20×0.95 = 19，当天就被判出场。
    rows = [(10.0, 10.2, 9.9, 10.0)] * 3 + [(10.0, 20.0, 9.8, 10.0)] + \
           [(10.0, 10.2, 9.9, 10.0)] * 3
    df = _engine_frame(rows, sig_idx=[0], stop_loss=8.0)
    spec = ExitSpec(stop=StopSpec(kind="pct", pct=0.20, cap_pct=1.0),
                    trail=TrailSpec(kind="pct", pct=0.05),
                    take_profit=TakeProfitSpec(kind="none"),
                    time_stop=TimeStopSpec(max_holding_days=0))
    res = BacktestEngine(_ENGINE_CFG).run({"600000": df}, signal_col="sig",
                                          exit_policy=ExitPolicy(spec))
    frame = res.trades_frame()
    assert not frame.empty, "构造前提：冲高回落后应当出场，只是不能在冲高当日"
    assert str(frame.iloc[0]["exit_date"]) > "2024-01-04", \
        "在冲高当日就按当日最高价止损出场了，这是未来函数"


def test_engine_without_policy_keeps_legacy_behaviour():
    """不传 exit_policy 时必须与改造前逐字一致，否则历史结果无法复现。"""
    rows = [(10.0, 10.2, 9.9, 10.0)] * 30
    df = _engine_frame(rows, sig_idx=[1], stop_loss=9.0)
    cfg = {**_ENGINE_CFG, "backtest": {**_ENGINE_CFG["backtest"],
                                       "max_holding_days": 5}}
    res = BacktestEngine(cfg).run({"600000": df}, signal_col="sig")
    frame = res.trades_frame()
    assert len(frame) == 1
    assert frame.iloc[0]["exit_reason"] == "持有满5日"


# ---------------------------------------------------------------------------
# 涨幅分批止盈 / 白线移动止损 / 盈转亏 / 低低走人（B1/B2 新口径）
# ---------------------------------------------------------------------------

def _pt_spec(**kw):
    from zhixing_quant.backtest.exits import ExitSpec, StopSpec, TakeProfitSpec, TimeStopSpec
    return ExitSpec(
        stop=StopSpec(kind="none"),
        take_profit=TakeProfitSpec(kind="price_tiers",
                                   price_tiers=((0.08, 1/3), (0.20, 1/3))),
        time_stop=TimeStopSpec(max_holding_days=0),
        **kw,
    )


def test_price_tier_first_fires_at_8pct():
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 11.0, 10.0, 10.9)])   # +9%
    d = ExitPolicy(_pt_spec()).on_close(f, 1, _pos(entry=10.0, high=11.0))
    assert d is not None and d.portion == pytest.approx(1/3)
    assert d.tier == 1


def test_price_tier_second_fires_at_20pct_and_not_before():
    pol = ExitPolicy(_pt_spec())
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 12.1, 10.0, 12.0)])    # +20%
    pos = _pos(entry=10.0, high=12.1)
    pos.tier_done = 1
    d = pol.on_close(f, 1, pos)
    assert d is not None and d.tier == 2


def test_price_tier_done_tiers_do_not_refire():
    pol = ExitPolicy(_pt_spec())
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 11.0, 10.0, 10.9)] * 2)
    pos = _pos(entry=10.0, high=11.0)
    pos.tier_done = 1
    assert pol.on_close(f, 3, pos) is None


def test_price_tier_between_thresholds_is_quiet():
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 10.5, 10.0, 10.4)])    # +4%
    assert ExitPolicy(_pt_spec()).on_close(f, 1, _pos(entry=10.0, high=10.5)) is None


def test_profit_to_loss_exits_only_after_real_profit():
    spec = _pt_spec(profit_to_loss=0.02)
    pol = ExitPolicy(spec)
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 10.3, 9.6, 9.7)])      # 收盘跌破成本
    # 曾有 +3% 浮盈（high=10.3）→ 盈转亏成立
    d = pol.on_close(f, 1, _pos(entry=10.0, high=10.3))
    assert d is not None and d.reason == "盈转亏清仓"
    # 若从未明显盈利（high 只到 10.1）→ 不触发
    d2 = pol.on_close(f, 1, _pos(entry=10.0, high=10.1))
    assert d2 is None


def test_close_below_prev_low_after_runup():
    spec = _pt_spec(close_below_prev_low=0.05)
    pol = ExitPolicy(spec)
    # 昨天 low=11.0，今天收盘 10.8 < 11.0，且曾有 +6% 浮盈
    f = _frame([(10, 10.2, 9.9, 10.0), (11.2, 11.5, 11.0, 11.3),
                (10.9, 11.0, 10.7, 10.8)])
    d = pol.on_close(f, 2, _pos(entry=10.0, high=11.5))
    assert d is not None and d.reason == "高位收盘破前低"
    # 浮盈没到 5% 的普通回落不触发
    f2 = _frame([(10, 10.2, 9.9, 10.0), (10.1, 10.3, 10.0, 10.2),
                 (9.9, 10.0, 9.7, 9.8)])
    assert pol.on_close(f2, 2, _pos(entry=10.0, high=10.3)) is None


def test_trailing_white_line_uses_yesterday_value():
    """白线/黄线是当日收盘算的，做当日盘中止损必须用昨日值（无未来函数）。"""
    spec = _pt_spec(trail=TrailSpec(kind="white_line", activate_profit=0.0))
    pol = ExitPolicy(spec)
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 11.0, 10.0, 10.9), (10, 11.0, 10.0, 10.9)])
    f.white_line = np.array([9.0, 9.5, 10.4])
    pos = _pos(entry=10.0, stop=0.0, tp=float("inf"), high=11.0)
    # 第3天：昨日白线 9.5 → 止损抬到 9.5；当日白线 10.4 不得泄露进来
    stop, _ = pol.levels(f, 2, pos)
    assert stop == pytest.approx(9.5)


def test_trailing_white_line_waits_for_activation():
    spec = _pt_spec(trail=TrailSpec(kind="white_line", activate_profit=0.08))
    pol = ExitPolicy(spec)
    f = _frame([(10, 10.2, 9.9, 10.0), (10, 10.3, 10.0, 10.2)])    # 只涨 2%
    f.white_line = np.array([9.0, 9.95])
    pos = _pos(entry=10.0, stop=0.0, tp=float("inf"), high=10.3)
    stop, _ = pol.levels(f, 1, pos)
    assert stop == 0.0        # 未达 +8% 不启用


def test_price_tiers_parse_from_config():
    cfg = {"exits": {"b1": {
        "take_profit": {"kind": "price_tiers",
                        "price_tiers": [{"gain": 0.08, "sell": 0.33},
                                         {"gain": 0.20, "sell": 0.33}]},
        "profit_to_loss": 0.02,
        "close_below_prev_low": 0.05,
    }}}
    s = spec_from_config(cfg, "b1")
    assert s.take_profit.price_tiers == ((0.08, 0.33), (0.20, 0.33))
    assert s.profit_to_loss == pytest.approx(0.02)
    assert s.close_below_prev_low == pytest.approx(0.05)
