"""2026-09 审计发现的运行期缺陷的回归测试。

每条测试对应一个**已复现**的 bug，不是防御性猜测。测试名里写清楚
"原来是什么行为"，以后有人把它改回去时能立刻看懂为什么不行。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.backtest.engine import BacktestEngine, Position
from zhixing_quant.backtest.metrics import compute_metrics
from zhixing_quant.scanner._core import scan_warnings
from zhixing_quant.signals.sell_s import detect_s_series


# ---------------------------------------------------------------------------
# 1. 盈亏比：无亏损交易时不得返回字符串
# ---------------------------------------------------------------------------

def test_profit_loss_ratio_is_always_formattable_number():
    """原来返回字符串 "inf"，回测页 f"{v:.2f}" 直接 ValueError 白屏。"""
    m = compute_metrics([100000, 101000, 102000],
                        [{"return_pct": 0.1}, {"return_pct": 0.2}])
    assert isinstance(m["profit_loss_ratio"], (int, float))
    f"{m['profit_loss_ratio']:.2f}"          # 不抛异常即通过
    assert m["profit_loss_ratio_is_inf"] is True


def test_profit_loss_ratio_not_flagged_infinite_when_losses_exist():
    m = compute_metrics([100000, 101000, 100500],
                        [{"return_pct": 0.1}, {"return_pct": -0.05}])
    assert m["profit_loss_ratio_is_inf"] is False
    assert m["profit_loss_ratio"] == pytest.approx(2.0)


def test_empty_metrics_carries_the_inf_flag():
    """界面无条件读这个键，缺了就是 KeyError。"""
    assert compute_metrics([], [])["profit_loss_ratio_is_inf"] is False


# ---------------------------------------------------------------------------
# 2. S1 的「跌破前低」分支曾经恒为假
# ---------------------------------------------------------------------------

def _falling_frame(n: int = 30) -> pd.DataFrame:
    """单边下跌：每天的收盘都低于前几日的最低，必然触发「跌破前低」。"""
    close = np.linspace(100.0, 70.0, n)
    idx = pd.date_range("2024-01-01", periods=n)
    df = pd.DataFrame({
        "open": close + 0.5, "high": close + 1.0,
        "low": close - 0.2, "close": close,
        "vol": [1_000_000.0] * n, "amount": [1e8] * n,
    }, index=idx)
    df["yellow_line"] = 1e9      # 关掉 break_yellow 分支，只测 break_low
    df["white_line"] = 1e9
    df["false_death_cross"] = False
    return df


def test_s1_break_prev_low_can_actually_fire():
    """原实现 low.rolling(5).min() 含当日，recent_low <= low <= close，
    所以 close < recent_low 在数学上永远不成立，这条分支一次都没跑过。"""
    out = detect_s_series(_falling_frame(), {})
    assert out["s1_break_prev_low"].sum() > 0, "跌破前低分支又变成死代码了"
    assert out["sig_s1"].sum() > 0


def test_s1_break_prev_low_window_excludes_today():
    """窗口一旦含当日就必定恒假——用一根当日创新低的 K 线直接锁死这一点。"""
    out = detect_s_series(_falling_frame(), {})
    last = out.iloc[-1]
    assert last["close"] > last["low"], "构造前提：收盘必然高于当日最低"
    assert bool(last["s1_break_prev_low"])


# ---------------------------------------------------------------------------
# 3. sell.* 配置曾是死配置（硬编码在函数体里）
# ---------------------------------------------------------------------------

def test_sell_thresholds_come_from_config():
    df = _falling_frame()
    loose = detect_s_series(df, {"sell": {"s1_break_low_window": 5}})
    strict = detect_s_series(
        df, {"sell": {"s1_break_low_window": 5, "s1_break_low_needs_volume": True}})
    assert int(loose["sig_s1"].sum()) > int(strict["sig_s1"].sum()), \
        "s1_break_low_needs_volume 不起作用，配置又断开了"


def test_s3_gap_down_threshold_is_configurable():
    n = 10
    idx = pd.date_range("2024-01-01", periods=n)
    # 每天低开 3%
    close = np.array([100.0 * (0.97 ** i) for i in range(n)])
    df = pd.DataFrame({
        "open": close * 1.001, "high": close * 1.01,
        "low": close * 0.99, "close": close,
        "vol": [1e6] * n, "amount": [1e8] * n,
    }, index=idx)
    df["yellow_line"] = 1e9
    df["white_line"] = 1e9
    df["false_death_cross"] = False
    fires = detect_s_series(df, {"sell": {"s3_gap_down_pct": 0.02}})["sig_s3"].sum()
    quiet = detect_s_series(df, {"sell": {"s3_gap_down_pct": 0.20}})["sig_s3"].sum()
    assert fires > quiet, "s3_gap_down_pct 没有被读取"


def test_sell_defaults_preserve_legacy_behaviour():
    """传空 cfg 时必须与修正前的硬编码阈值一致，否则是悄悄改了口径。"""
    from zhixing_quant.signals.sell_s import DEFAULTS
    assert DEFAULTS["s1_double_vol_threshold"] == 2.0
    assert DEFAULTS["s3_big_bear_body_min"] == 0.6
    assert DEFAULTS["s3_gap_down_pct"] == 0.02


# ---------------------------------------------------------------------------
# 4. 跳空穿越止损时的成交价
# ---------------------------------------------------------------------------

def _pos(stop: float = 9.5, tp: float = 11.5) -> Position:
    return Position(code="600000", entry_date="2024-01-01", entry_price=10.0,
                    shares=1000, cost_basis=10000.0, stop_loss=stop, take_profit=tp)


def test_stop_fill_uses_open_when_price_gaps_through():
    """止损挂 9.50，次日直接低开到 8.00。原实现按 9.50 记账，
    等于假设每次跳空都能在缺口上沿接住，系统性高估收益。"""
    bar = {"i": 0, "open": 8.0, "high": 8.2, "low": 7.9, "close": 8.0}
    hit, price, reason = BacktestEngine._intraday_exit(bar, _pos())
    assert hit and reason == "止损"
    assert price == 8.0, f"跳空穿越后仍按 {price} 成交，回测又开始虚增了"


def test_stop_fill_uses_stop_price_when_intraday_touch():
    """盘中触及（开盘在止损之上）仍按止损价成交，这是正常情况。"""
    bar = {"i": 0, "open": 10.0, "high": 10.1, "low": 9.4, "close": 9.6}
    hit, price, reason = BacktestEngine._intraday_exit(bar, _pos())
    assert hit and reason == "止损" and price == 9.5


def test_take_profit_fill_uses_open_when_gapping_up():
    bar = {"i": 0, "open": 12.5, "high": 12.8, "low": 12.4, "close": 12.6}
    hit, price, reason = BacktestEngine._intraday_exit(bar, _pos())
    assert hit and reason == "止盈" and price == 12.5


def test_stop_beats_take_profit_on_the_same_bar():
    """同一根 K 线无法判先后，必须保守取止损。"""
    bar = {"i": 0, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}
    hit, price, reason = BacktestEngine._intraday_exit(bar, _pos())
    assert reason == "止损"


# ---------------------------------------------------------------------------
# 5. 防守阶段的决策日必须可指定（历史复盘不得读到未来 K 线）
# ---------------------------------------------------------------------------

def test_stage_defense_accepts_as_of():
    """原实现调 load_daily(code) 不带 end_date，复盘历史日期时
    防守引擎用的是今天的收盘价。"""
    import inspect

    from zhixing_quant.executor.daily_workflow import _stage_defense

    sig = inspect.signature(_stage_defense)
    assert "as_of" in sig.parameters
    src = inspect.getsource(_stage_defense)
    assert "end_date=as_of" in src, "as_of 没有真的传进 load_daily"


def test_run_daily_cycle_passes_as_of_to_defense():
    import inspect

    from zhixing_quant.executor.daily_workflow import run_daily_cycle

    src = inspect.getsource(run_daily_cycle)
    assert "as_of=" in src, "主循环没把决策日传给防守阶段"


# ---------------------------------------------------------------------------
# 6. 扫描器不得静默吞掉指标异常
# ---------------------------------------------------------------------------

def test_scan_warnings_reports_indicator_failures():
    out = scan_warnings(100, 0, {f"{i:06d}": "ValueError: boom" for i in range(7)})
    assert any("7 只" in w for w in out)


def test_scan_warnings_flags_mostly_short_history():
    out = scan_warnings(100, 80, {})
    assert any("K 线不足" in w for w in out)


def test_scan_warnings_flags_nothing_computed():
    out = scan_warnings(50, 50, {})
    assert any("无意义" in w for w in out)


def test_scan_warnings_silent_when_healthy():
    assert scan_warnings(100, 3, {}) == []
