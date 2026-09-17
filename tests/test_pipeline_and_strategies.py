"""指标流水线 / 战法适配器 测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.indicators.pipeline import (
    DEFENSE_REQUIRED, PIPELINES, defense_coverage, run_pipeline, run_steps,
)


@pytest.fixture
def cfg():
    from zhixing_quant.config import load_config
    return load_config()


@pytest.fixture
def bars():
    """160 根合成日线，够算 MA114 和 60 日背离窗口。"""
    n = 160
    idx = pd.DatetimeIndex(pd.date_range("2023-01-02", periods=n, freq="B"), name="date")
    rng = np.random.default_rng(7)
    close = 20 * np.cumprod(1 + rng.normal(0, 0.018, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(5_000_000, 60_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "vol": vol, "amount": vol * close}, index=idx)


# ---------------------------------------------------------------------------
# 流水线
# ---------------------------------------------------------------------------

def test_defense_pipeline_runs_all_steps(cfg, bars):
    """核心回归：这些指标模块曾经因签名不符被静默跳过。"""
    r = run_pipeline(bars, cfg, "defense")
    assert r.ok, f"有步骤失败：{r.failed}"
    assert set(r.applied) == set(PIPELINES["defense"])


def test_all_defense_rules_have_their_columns(cfg, bars):
    """防守八级阶梯不能有哑规则。"""
    r = run_pipeline(bars, cfg, "defense")
    dead = [rule for rule, ok in defense_coverage(r.df).items() if not ok]
    assert not dead, f"以下规则缺列不会触发：{dead}"


def test_full_pipeline_runs(cfg, bars):
    r = run_pipeline(bars, cfg, "full")
    assert r.ok, f"有步骤失败：{r.failed}"


def test_failure_is_reported_not_swallowed(cfg, bars):
    """未注册的步骤要出现在 failed 里，不能悄悄跳过。"""
    r = run_steps(bars, cfg, ["brick", "no_such_step"])
    assert "brick" in r.applied
    assert not r.ok
    assert r.failed[0][0] == "no_such_step"
    assert r.warnings()


def test_unknown_pipeline_raises(cfg, bars):
    with pytest.raises(KeyError):
        run_pipeline(bars, cfg, "not_a_pipeline")


def test_macd_divergence_actually_computed(cfg, bars):
    """add_macd 曾经因 NameError 崩溃（局部变量叫 close，循环里写的 price）。

    旧测试只有 30 根 K 线，而 divergence_window=60，那段循环从没执行过。
    这条用 160 根确保循环真的跑到。
    """
    r = run_steps(bars, cfg, ["macd"])
    assert r.ok, r.failed
    assert "bull_divergence" in r.df.columns
    assert len(r.df) > int(cfg.get("macd", {}).get("divergence_window", 60))


def test_defense_required_columns_declared():
    """DEFENSE_REQUIRED 是界面判断哑规则的依据，不能为空。"""
    assert DEFENSE_REQUIRED
    for rule, cols in DEFENSE_REQUIRED.items():
        assert cols, f"{rule} 没有声明依赖列"


# ---------------------------------------------------------------------------
# 战法适配器
# ---------------------------------------------------------------------------

def test_four_strategies_registered():
    from zhixing_quant.scanner.strategy_scan import available

    got = available()
    assert set(got) == {"brick", "b1", "b2", "single_needle"}


def test_every_registered_strategy_has_a_pipeline():
    """registry 注册了但没声明流水线的战法扫不了，等于白注册。"""
    from zhixing_quant.strategies.registry import STRATEGY_REGISTRY

    missing = [n for n in STRATEGY_REGISTRY if n not in PIPELINES]
    assert not missing, f"这些战法缺 PIPELINES 声明：{missing}"


def test_build_spec_for_each_strategy(cfg):
    from zhixing_quant.scanner.strategy_scan import available, build_spec

    for name in available():
        spec = build_spec(name, cfg)
        assert spec is not None, name
        assert spec.signal_col == "sig_strategy"
        assert spec.min_bars(cfg) >= 120       # 黄线含 MA114，规格 00.3


def test_build_spec_unknown_returns_none(cfg):
    from zhixing_quant.scanner.strategy_scan import build_spec

    assert build_spec("no_such_strategy", cfg) is None


def test_adapter_does_not_clobber_indicator_stop_loss(cfg, bars):
    """砖型图指标自带 stop_loss 列，适配器不能用 NaN 覆盖它。"""
    from zhixing_quant.scanner.strategy_scan import build_spec

    spec = build_spec("brick", cfg)
    out = spec.add_indicators(bars, cfg)
    assert "stop_loss" in out.columns
    assert out["stop_loss"].notna().any(), "止损列被整列覆盖成 NaN"


def test_strategy_meta_covers_all_strategies():
    from zhixing_quant.scanner.strategy_scan import STRATEGY_META, available

    for name in available():
        assert name in STRATEGY_META, f"{name} 缺少中文名与账户归属"
        assert STRATEGY_META[name]["book"] in ("swing", "scalp")


def test_exit_reason_is_translated():
    """DefenseEngine 返回英文代号，界面上不该出现 stop_loss_hit。"""
    from zhixing_quant.ui.components import exit_reason

    assert exit_reason("stop_loss_hit") == "触发止损位"
    assert exit_reason("yellow_break") == "跌破黄线"
    assert exit_reason("未知代号") == "未知代号"      # 未映射时原样返回，不报错
