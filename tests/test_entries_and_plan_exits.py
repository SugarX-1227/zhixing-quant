"""建仓规则（底仓 + 分批加仓）与规划书 B1/B2 出场口径的测试。

对应规划书：
    6.2.1 B1 五步循环   建底仓 10-20% / 分批加仓 / 放飞 1/3 / 破白线减半 / 破黄线全清
    6.3   三大 B2 共享出场   S1 减 50% / 跌破入场低点全清 / 2 日不拉升全清 / 出货减 50%
    4.3.2 sig_b2_hold_max = 2（仅允许休整 1 天）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.backtest.engine import BacktestEngine, Position
from zhixing_quant.backtest.exits import (ExitFrame, ExitPolicy, ExitSpec,
                                          StopSpec, TakeProfitSpec, TimeStopSpec,
                                          TrailSpec, required_steps,
                                          spec_from_config)
from zhixing_quant.portfolio.sizer import EntrySpec, entry_spec_from_config

CFG = {
    "backtest": {"initial_capital": 1_000_000, "commission": 0.0,
                 "commission_min": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0,
                 "slippage": 0.0, "max_positions": 3, "max_holding_days": 999,
                 "max_entries_per_day": 3, "limit_up_pct": 0.10,
                 "sizing": "equal"},
    "execution": {"abandon_gap_up": 0.99},
}


def _frame(rows, sig_idx=(), **cols) -> pd.DataFrame:
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


def _exit_frame(rows, **cols) -> ExitFrame:
    return ExitFrame(_frame(rows, **cols))


def _pos(entry=10.0, shares=1000, high=10.0, held=1) -> Position:
    return Position(code="600000", entry_date="2024-01-01", entry_price=entry,
                    shares=shares, cost_basis=entry * shares, stop_loss=0.0,
                    take_profit=float("inf"), bars_held=held,
                    entry_shares=shares, entry_low=entry * 0.98, highest_high=high)


# ---------------------------------------------------------------------------
# EntrySpec
# ---------------------------------------------------------------------------

def test_entry_spec_defaults_keep_the_one_shot_behaviour():
    s = entry_spec_from_config({})
    assert s.base_pct == 0.0 and not s.scales_in
    assert "一次建满" in s.describe()


def test_entry_spec_strategy_section_overrides_default():
    cfg = {"entries": {"default": {"base_pct": 0.0},
                       "b1": {"base_pct": 0.15, "addon_pct": 0.05,
                              "max_addons": 4}}}
    s = entry_spec_from_config(cfg, "b1")
    assert s.base_pct == 0.15 and s.scales_in and s.max_addons == 4
    assert entry_spec_from_config(cfg, "b2").base_pct == 0.0


def test_shipped_config_matches_the_plan_for_b1():
    """规划书 6.2.1：底仓 10-20%，每次 sig_b1 加 5%，最多 4 次。"""
    from zhixing_quant.config import load_config

    s = entry_spec_from_config(load_config(), "b1")
    assert 0.10 <= s.base_pct <= 0.20
    assert s.addon_pct == pytest.approx(0.05)
    assert s.max_addons == 4


# ---------------------------------------------------------------------------
# 分批加仓
# ---------------------------------------------------------------------------

def test_engine_scales_in_on_repeated_signals():
    """同一只票反复出信号时应该加仓，而不是像以前那样直接跳过。"""
    rows = [(10.0, 10.2, 9.9, 10.0)] * 20
    df = _frame(rows, sig_idx=[1, 3, 5, 7, 9, 11])
    df["stop_loss"] = 8.0
    spec = EntrySpec(base_pct=0.15, addon_pct=0.05, max_addons=4)
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  entry_spec=spec)
    snap = res.daily_positions[-1]["positions"]
    assert snap, "应该还持有仓位"
    # 底仓 15% + 4 次 5% = 35% of 1,000,000 = 350,000 市值（约 35000 股 @10）
    assert snap[0]["shares"] == 35000, f"实际 {snap[0]['shares']} 股，加仓没按规划书走"


def test_scale_in_stops_at_max_addons():
    rows = [(10.0, 10.2, 9.9, 10.0)] * 30
    df = _frame(rows, sig_idx=list(range(1, 25)))
    df["stop_loss"] = 8.0
    spec = EntrySpec(base_pct=0.10, addon_pct=0.05, max_addons=2)
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  entry_spec=spec)
    snap = res.daily_positions[-1]["positions"]
    assert snap[0]["shares"] == 20000, "加仓次数没有被 max_addons 封住"


def test_no_scale_in_when_disabled():
    rows = [(10.0, 10.2, 9.9, 10.0)] * 20
    df = _frame(rows, sig_idx=[1, 3, 5, 7])
    df["stop_loss"] = 8.0
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  entry_spec=EntrySpec(base_pct=0.10))
    snap = res.daily_positions[-1]["positions"]
    assert snap[0]["shares"] == 10000, "没开加仓却加了仓"


def test_scale_in_averages_the_entry_price():
    """加仓后成本价必须是加权平均，否则后面的止盈档位和盈转亏判定全错。

    底仓 10% 权益 = 100,000 @10 → 10000 股，现金剩 900,000。
    次日价格到 20，权益 = 900,000 + 10000×20 = 1,100,000，
    加仓 10% = 110,000 @20 → 5500 股。
    合计 15500 股，加权均价 = (10×10000 + 20×5500) / 15500 = 13.548。
    """
    rows = [(10.0, 10.2, 9.9, 10.0)] * 3 + [(20.0, 20.2, 19.9, 20.0)] * 11
    df = _frame(rows, sig_idx=[0, 3])
    # 关掉一切价位型出场，只留时间止损，好让整段持仓走到底再平
    policy = ExitPolicy(ExitSpec(stop=StopSpec(kind="none"),
                                 take_profit=TakeProfitSpec(kind="none"),
                                 time_stop=TimeStopSpec(max_holding_days=8)))
    spec = EntrySpec(base_pct=0.10, addon_pct=0.10, max_addons=1)
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  entry_spec=spec, exit_policy=policy)
    frame = res.trades_frame()
    assert len(frame) == 1, f"应只有一笔平仓记录，实际 {len(frame)}"
    t = frame.iloc[0]
    assert int(t["shares"]) == 15500, "加仓股数不对"
    assert float(t["entry_price"]) == pytest.approx(13.548, abs=0.01), \
        "成本价不是加权平均"


def test_addon_min_gain_gates_the_addon():
    rows = [(10.0, 10.2, 9.9, 10.0)] + [(8.0, 8.2, 7.9, 8.0)] * 10
    df = _frame(rows, sig_idx=[0, 2, 4])
    df["stop_loss"] = 1.0
    greedy = EntrySpec(base_pct=0.10, addon_pct=0.10, max_addons=3,
                       addon_min_gain=-1.0)
    picky = EntrySpec(base_pct=0.10, addon_pct=0.10, max_addons=3,
                      addon_min_gain=0.05)
    a = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                entry_spec=greedy).daily_positions[-1]["positions"]
    b = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                entry_spec=picky).daily_positions[-1]["positions"]
    assert a[0]["shares"] > b[0]["shares"], "addon_min_gain 没拦住浮亏时的加仓"


# ---------------------------------------------------------------------------
# 破白线减半（规划书 6.2.1 第 5 步）
# ---------------------------------------------------------------------------

def test_white_line_break_is_a_partial_exit():
    spec = ExitSpec(white_line_break=0.5,
                    time_stop=TimeStopSpec(max_holding_days=0))
    f = _exit_frame([(10, 10.5, 9.0, 9.2)] * 3, white_line=10.0)
    d = ExitPolicy(spec).on_close(f, 2, _pos())
    assert d is not None and d.portion == pytest.approx(0.5)
    assert "减50%" in d.reason


def test_white_line_break_can_be_configured_as_full_exit():
    spec = ExitSpec(white_line_break=1.0,
                    time_stop=TimeStopSpec(max_holding_days=0))
    f = _exit_frame([(10, 10.5, 9.0, 9.2)] * 3, white_line=10.0)
    d = ExitPolicy(spec).on_close(f, 2, _pos())
    assert d.portion == 1.0 and "清仓" in d.reason


def test_white_line_break_fires_only_once_per_position():
    """不加这个闸门，价格在白线下待一周就会被连着减七次。"""
    spec = ExitSpec(white_line_break=0.5,
                    time_stop=TimeStopSpec(max_holding_days=0))
    f = _exit_frame([(10, 10.5, 9.0, 9.2)] * 3, white_line=10.0)
    pos = _pos()
    pos.white_break_done = True
    assert ExitPolicy(spec).on_close(f, 2, pos) is None


def test_white_line_break_ignores_nan_line():
    spec = ExitSpec(white_line_break=0.5,
                    time_stop=TimeStopSpec(max_holding_days=0))
    f = _exit_frame([(10, 10.5, 9.0, 9.2)] * 3, white_line=np.nan)
    assert ExitPolicy(spec).on_close(f, 2, _pos()) is None


def test_engine_marks_white_break_done_so_it_does_not_repeat():
    rows = [(10.0, 10.2, 9.9, 10.0)] * 2 + [(9.0, 9.1, 8.9, 9.0)] * 8
    df = _frame(rows, sig_idx=[0], white_line=10.5, yellow_line=1.0)
    df["stop_loss"] = 1.0
    spec = ExitSpec(stop=StopSpec(kind="none"),
                    take_profit=TakeProfitSpec(kind="none"),
                    time_stop=TimeStopSpec(max_holding_days=0),
                    white_line_break=0.5)
    res = BacktestEngine(CFG).run({"600000": df}, signal_col="sig",
                                  exit_policy=ExitPolicy(spec))
    breaks = [t for t in res.trades if "跌破白线" in t.exit_reason]
    assert len(breaks) == 1, f"破白线减仓触发了 {len(breaks)} 次，应该只有 1 次"


# ---------------------------------------------------------------------------
# 防守阶梯：只开指定级别 + 减半
# ---------------------------------------------------------------------------

def _defense_frame(**flags) -> ExitFrame:
    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    df = pd.DataFrame({"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.0,
                       "vol": 1e6, "amount": 1e9,
                       "yellow_line": 1.0, "white_line": 1.0}, index=idx)
    for k, v in flags.items():
        df[k] = [False, False, v]
    return ExitFrame(df)


def test_defense_rules_allowlist_filters_the_ladder():
    from zhixing_quant.portfolio.defense import DefenseEngine

    f = _defense_frame(sig_s1=True)
    on = ExitPolicy(ExitSpec(defense_ladder=True, defense_rules=("s1",),
                             time_stop=TimeStopSpec(max_holding_days=0)),
                    defense=DefenseEngine())
    off = ExitPolicy(ExitSpec(defense_ladder=True, defense_rules=("distribution",),
                              time_stop=TimeStopSpec(max_holding_days=0)),
                     defense=DefenseEngine())
    assert on.on_close(f, 2, _pos()) is not None
    assert off.on_close(f, 2, _pos()) is None, "白名单没把 s1 挡在外面"


def test_defense_s1_reduces_by_half_not_full():
    """规划书 6.3 第 1 条：S1 → 减 50%，不是清仓。"""
    from zhixing_quant.portfolio.defense import DefenseEngine

    f = _defense_frame(sig_s1=True)
    p = ExitPolicy(ExitSpec(defense_ladder=True, defense_rules=("s1",),
                            defense_s1_portion=0.5,
                            time_stop=TimeStopSpec(max_holding_days=0)),
                   defense=DefenseEngine())
    assert p.on_close(f, 2, _pos()).portion == pytest.approx(0.5)


def test_defense_distribution_reduces_by_half():
    from zhixing_quant.portfolio.defense import DefenseEngine

    f = _defense_frame(sig_distribution=True)
    p = ExitPolicy(ExitSpec(defense_ladder=True, defense_rules=("distribution",),
                            defense_distribution_portion=0.5,
                            time_stop=TimeStopSpec(max_holding_days=0)),
                   defense=DefenseEngine())
    d = p.on_close(f, 2, _pos())
    assert d is not None and d.portion == pytest.approx(0.5)


def test_defense_portion_defaults_to_full_exit():
    from zhixing_quant.portfolio.defense import DefenseEngine

    f = _defense_frame(sig_s1=True)
    p = ExitPolicy(ExitSpec(defense_ladder=True, defense_rules=("s1",),
                            time_stop=TimeStopSpec(max_holding_days=0)),
                   defense=DefenseEngine())
    assert p.on_close(f, 2, _pos()).portion == 1.0


# ---------------------------------------------------------------------------
# 出场规则的指标依赖：不声明就是哑开关
# ---------------------------------------------------------------------------

def test_required_steps_pulls_in_defense_indicators():
    steps = required_steps(ExitSpec(defense_ladder=True,
                                    defense_rules=("s1", "distribution")))
    assert "sell_s" in steps and "distribution" in steps and "dual_line" in steps


def test_required_steps_pulls_dual_line_for_line_rules():
    assert "dual_line" in required_steps(ExitSpec(break_yellow_line=True))
    assert "dual_line" in required_steps(ExitSpec(white_line_break=0.5))
    assert "dual_line" in required_steps(
        ExitSpec(trail=TrailSpec(kind="yellow_line")))


def test_required_steps_pulls_brick_for_tiered_take_profit():
    assert "brick" in required_steps(
        ExitSpec(take_profit=TakeProfitSpec(kind="tiered", tiers={2: 0.1})))


def test_required_steps_is_empty_for_a_bare_spec():
    assert required_steps(ExitSpec(stop=StopSpec(kind="pct"),
                                   take_profit=TakeProfitSpec(kind="pct"))) == []


def test_runner_merges_required_steps_into_the_pipeline():
    """否则 defense_ladder: true 就是个哑开关——列不存在，规则静默失效。"""
    import inspect

    from zhixing_quant.backtest import runner

    src = inspect.getsource(runner.run_backtest)
    assert "required_steps" in src
    assert "run_steps" in src


# ---------------------------------------------------------------------------
# 随仓配置是否真的落到了规划书的口径上
# ---------------------------------------------------------------------------

def test_shipped_b2_disabled_the_two_rules_the_data_rejected():
    """B2 的「2 日不拉升」和「低低走人」已按实测关闭。

    这两条原本是按规划书 4.3.2 / 6.3 加上去的，但拿真实行情
    （800 只 / 2022-08~2026-09）跑消融，它们是**亏钱**的：

        关掉 低低走人      总收益 +26.4%
        关掉 2日不拉升     总收益 +5.9%

    并且样本内外都改善（样本内 -37.6%→-33.2%，样本外 +11.6%→+46.4%），
    过了「删规则必须过样本外」这一关，才动的配置。

    规划书是**假设**，实测是**证据**。这条测试锁的是证据，不是假设——
    要改回去，先拿出新的样本外证据。
    """
    from zhixing_quant.config import load_config

    spec = spec_from_config(load_config(), "b2")
    assert spec.time_stop.no_progress_days == 0, "2日不拉升实测亏钱，应保持关闭"
    assert spec.close_below_prev_low == 0.0, "低低走人实测吃掉 26 个百分点，应保持关闭"


def test_shipped_b1_still_keeps_the_low_low_rule():
    """B1 的「低低走人」没动——消融只在 B2 上做过，别把结论外推。"""
    from zhixing_quant.config import load_config

    assert spec_from_config(load_config(), "b1").close_below_prev_low > 0


def test_shipped_b2_reduces_by_half_on_s1_and_distribution():
    from zhixing_quant.config import load_config

    s = spec_from_config(load_config(), "b2")
    assert s.defense_ladder and set(s.defense_rules) == {"s1", "distribution"}
    assert s.defense_s1_portion == pytest.approx(0.5)
    assert s.defense_distribution_portion == pytest.approx(0.5)


def test_shipped_b1_halves_on_white_break_and_clears_on_yellow():
    from zhixing_quant.config import load_config

    s = spec_from_config(load_config(), "b1")
    assert s.white_line_break == pytest.approx(0.5), "规划书是破白线减半"
    assert s.break_yellow_line is True, "规划书是破黄线全清"


def test_shipped_b1_flies_a_third_at_five_percent():
    """规划书 6.2.1 第 4 步：脱离成本 5%+ 放飞底仓的 1/3。"""
    from zhixing_quant.config import load_config

    tiers = spec_from_config(load_config(), "b1").take_profit.price_tiers
    assert tiers, "B1 的分批止盈档位空了"
    gain, sell = tiers[0]
    assert gain == pytest.approx(0.05)
    assert sell == pytest.approx(1 / 3, abs=0.01)
