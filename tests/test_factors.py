"""因子层测试。

重点在两件事：
1. 横截面标准化必须在**单日内**做，不能跨日期（跨日期用到全样本统计量
   就是未来函数，IC 会虚高）。
2. 因子本身不许读未来数据。唯一允许 shift(-n) 的是 evaluate.forward_returns，
   它是被解释变量。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import zhixing_quant.factors as FA
from zhixing_quant.factors.base import (FACTOR_REGISTRY, Factor, list_factors,
                                        register_factor, required_steps)
from zhixing_quant.factors.cross_section import (build_panel, coverage,
                                                 rank_codes, score_panel,
                                                 standardize, winsorize, zscore)
from zhixing_quant.factors.evaluate import (evaluate_factors, factor_ic,
                                            forward_returns, ic_summary,
                                            monotonicity, quantile_returns)
from zhixing_quant.factors.library import PRESETS, preset_weights


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _stock(seed: int, n: int = 200, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = np.clip(20 + np.cumsum(rng.normal(drift, 0.5, n)), 1, None)
    prev = np.concatenate([[20.0], close[:-1]])
    return pd.DataFrame({
        "open": prev,
        "high": np.maximum(close, prev) * 1.01,
        "low": np.minimum(close, prev) * 0.99,
        "close": close,
        "vol": rng.integers(1e6, 9e6, n).astype(float),
        "amount": (close * rng.integers(1e6, 9e6, n)).astype(float),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


def _market(k: int = 12, n: int = 200) -> dict:
    return {f"{600000 + i:06d}": _stock(i, n, drift=0.02 * (i - k / 2))
            for i in range(k)}


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def test_library_registers_factors_across_categories():
    facs = list_factors()
    assert len(facs) >= 12
    assert len({f.category for f in facs}) >= 4


def test_every_factor_declares_a_direction():
    for f in list_factors():
        assert f.direction in (1, -1), f"{f.name} 的方向必须是 +1 或 -1"


def test_every_factor_has_a_chinese_label_and_help():
    for f in list_factors():
        assert f.label and f.label != f.name, f"{f.name} 缺中文名"


def test_required_steps_collects_dependencies():
    steps = required_steps(["dist_yellow", "brick_red_height", "mom_20"])
    assert "dual_line" in steps and "brick" in steps


def test_required_steps_ignores_unknown_names():
    assert required_steps(["根本没有这个因子"]) == []


def test_register_factor_is_idempotent_by_name():
    n0 = len(FACTOR_REGISTRY)
    f = Factor(name="__tmp__", label="临时", category="测试",
               compute=lambda df: pd.Series(1.0, index=df.index))
    register_factor(f)
    register_factor(f)
    assert len(FACTOR_REGISTRY) == n0 + 1
    FACTOR_REGISTRY.pop("__tmp__")


# ---------------------------------------------------------------------------
# 因子计算：不许读未来
# ---------------------------------------------------------------------------

def test_no_factor_reads_the_future():
    """截断掉后半段数据，前半段的因子值必须逐位不变。

    任何 shift(-n) 或全样本统计量都会让这条失败。
    """
    df = _stock(1, 200)
    half = df.iloc[:120]
    for f in list_factors():
        full = f.compute(df).iloc[:120]
        cut = f.compute(half)
        a, b = full.to_numpy(float), cut.to_numpy(float)
        both = np.isfinite(a) & np.isfinite(b)
        assert np.allclose(a[both], b[both], equal_nan=True), \
            f"{f.name} 用到了未来数据：截断后前半段的值变了"


def test_factors_return_series_aligned_to_input():
    df = _stock(2, 150)
    for f in list_factors():
        s = f.compute(df)
        assert len(s) == len(df), f"{f.name} 返回长度不对"
        assert s.index.equals(df.index), f"{f.name} 返回索引不对"


def test_factors_degrade_to_nan_when_columns_missing():
    """缺指标列时必须返回 NaN 而不是抛异常——否则一个因子能搞崩整轮扫描。"""
    bare = _stock(3, 150)[["open", "high", "low", "close", "vol", "amount"]]
    for f in list_factors():
        s = f.compute(bare)          # 不抛异常即可
        assert isinstance(s, pd.Series)


# ---------------------------------------------------------------------------
# 横截面标准化
# ---------------------------------------------------------------------------

def test_winsorize_clips_outliers_by_mad():
    s = pd.Series([1.0, 2, 3, 4, 5, 1000])
    out = winsorize(s, n_mad=3)
    assert out.max() < 1000
    assert out.iloc[:5].equals(s.iloc[:5])


def test_winsorize_survives_constant_series():
    s = pd.Series([5.0] * 6)
    assert winsorize(s).equals(s)


def test_zscore_of_constant_series_is_zero_not_nan():
    out = zscore(pd.Series([3.0, 3.0, 3.0]))
    assert (out == 0).all(), "全同值应返回 0，NaN 会让这只票被当成算不出因子"


def test_standardize_applies_direction():
    s = pd.Series([1.0, 2.0, 3.0])
    up = standardize(s, direction=1)
    down = standardize(s, direction=-1)
    assert up.iloc[-1] > up.iloc[0]
    assert down.iloc[-1] < down.iloc[0], "方向为 -1 时应该反过来"


def test_standardize_keeps_nan_as_nan():
    s = pd.Series([1.0, np.nan, 3.0])
    assert standardize(s).isna().iloc[1]


# ---------------------------------------------------------------------------
# 面板与打分
# ---------------------------------------------------------------------------

def test_build_panel_has_date_code_multiindex():
    panel = build_panel(_market(5, 150), ["mom_20", "vol_ratio"])
    assert list(panel.index.names) == ["date", "code"]
    assert set(panel.columns) == {"mom_20", "vol_ratio"}


def test_build_panel_can_restrict_to_dates():
    data = _market(4, 150)
    day = list(data.values())[0].index[-1]
    panel = build_panel(data, ["mom_20"], dates=[day])
    assert panel.index.get_level_values("date").unique().tolist() == [day]


def test_coverage_flags_a_factor_that_never_computes():
    data = _market(4, 150)
    panel = build_panel(data, ["mom_20", "dist_yellow"])
    cov = coverage(panel)
    assert cov["dist_yellow"] == 0.0, "没有 yellow_line 列时该因子应全 NaN"
    assert cov["mom_20"] > 0.5


def test_score_panel_is_computed_within_each_day():
    """同一天内标准化：把某一天整体平移一个常数，当日排序不应改变。"""
    data = _market(8, 150)
    panel = build_panel(data, ["mom_20"])
    weights = {"mom_20": 1.0}
    base = score_panel(panel, weights, min_names=3)

    shifted = panel.copy()
    # 取靠后的日期，保证 mom_20 已经有值（前 20 根是 NaN，比不出排序）
    day = panel.index.get_level_values("date").unique()[-5]
    mask = shifted.index.get_level_values("date") == day
    shifted.loc[mask, "mom_20"] += 100.0        # 整日平移
    after = score_panel(shifted, weights, min_names=3)

    a = base[mask].rank().to_numpy()
    b = after[mask].rank().to_numpy()
    assert np.isfinite(a).all(), "构造前提：这一天应该人人有分"
    assert np.allclose(a, b), "当日整体平移改变了排序，说明标准化跨了日期"


def test_score_panel_skips_thin_cross_sections():
    data = _market(2, 150)
    panel = build_panel(data, ["mom_20"])
    out = score_panel(panel, {"mom_20": 1.0}, min_names=5)
    assert out.isna().all(), "横截面只有 2 只时不该给分"


def test_score_panel_without_weights_is_all_nan_not_zero():
    """没有权重时每行都该是「没有分数」，不能是 0——0 会被当成中等水平。"""
    panel = build_panel(_market(5, 150), ["mom_20"])
    out = score_panel(panel, {})
    assert out.index.equals(panel.index)
    assert out.isna().all()


def test_score_panel_ignores_unknown_factor_names():
    panel = build_panel(_market(6, 150), ["mom_20"])
    out = score_panel(panel, {"mom_20": 1.0, "不存在": 5.0}, min_names=3)
    assert out.notna().any()


def test_rank_codes_orders_by_composite_score():
    data = _market(10, 180)
    day = list(data.values())[0].index[-1]
    out = rank_codes(data, list(data), day, {"mom_20": 1.0, "vol_ratio": 0.5})
    assert not out.empty
    assert list(out["score"]) == sorted(out["score"], reverse=True)
    assert set(out["code"]) <= set(data)


def test_rank_codes_empty_without_weights():
    data = _market(4, 150)
    day = list(data.values())[0].index[-1]
    assert rank_codes(data, list(data), day, {}).empty


# ---------------------------------------------------------------------------
# 有效性检验
# ---------------------------------------------------------------------------

def test_forward_returns_look_ahead_by_horizon():
    df = _stock(4, 50)
    fwd = forward_returns({"600000": df}, horizon=3)
    s = fwd.xs("600000", level="code")
    expected = df["close"].shift(-3) / df["close"] - 1.0
    assert np.allclose(s.dropna().to_numpy(), expected.dropna().to_numpy())
    assert s.iloc[-3:].isna().all(), "最后 horizon 根必须是 NaN"


def test_factor_ic_detects_a_planted_signal():
    """种一个「因子值就是未来收益」的作弊因子，IC 必须接近 1。

    这是对 IC 计算本身的自检：如果连完美因子都测不出来，
    那真实因子的 0.02 更没法信。
    """
    data = _market(20, 160)
    fwd = forward_returns(data, horizon=5)
    cheat = fwd.rename("cheat").to_frame()
    ic = factor_ic(cheat, fwd, min_names=5)
    assert not ic.empty
    assert ic["cheat"].mean() > 0.95


def test_factor_ic_of_pure_noise_is_near_zero():
    rng = np.random.default_rng(0)
    data = _market(20, 160)
    fwd = forward_returns(data, horizon=5)
    noise = pd.Series(rng.normal(size=len(fwd)), index=fwd.index, name="noise")
    ic = factor_ic(noise.to_frame(), fwd, min_names=5)
    assert abs(ic["noise"].mean()) < 0.15


def test_ic_summary_columns_and_ordering():
    data = _market(20, 160)
    panel = build_panel(data, ["mom_20", "vol_ratio", "atr_pct"])
    fwd = forward_returns(data, horizon=5)
    summary = ic_summary(factor_ic(panel, fwd, min_names=5))
    assert set(["因子", "IC均值", "ICIR", "t值", "正IC占比", "有效天数"]) <= set(summary.columns)
    icirs = summary["ICIR"].abs().to_numpy()
    assert (np.diff(icirs) <= 1e-9).all(), "应按 |ICIR| 降序"


def test_ic_summary_handles_empty_input():
    out = ic_summary(pd.DataFrame())
    assert out.empty and "ICIR" in out.columns


def test_quantile_returns_shape():
    data = _market(25, 160)
    panel = build_panel(data, ["mom_20"])
    fwd = forward_returns(data, horizon=5)
    q = quantile_returns(panel, fwd, "mom_20", q=5, min_names=5)
    assert list(q.index) == [f"Q{i}" for i in range(1, 6)]
    assert q["样本数"].sum() > 0


def test_quantile_returns_is_monotonic_for_a_perfect_factor():
    data = _market(25, 160)
    fwd = forward_returns(data, horizon=5)
    cheat = fwd.rename("cheat").to_frame()
    q = quantile_returns(cheat, fwd, "cheat", q=5, min_names=5)
    assert monotonicity(q) == 1.0, "完美因子的分层收益必须完全单调"


def test_monotonicity_of_empty_table_is_zero():
    assert monotonicity(pd.DataFrame()) == 0.0


def test_evaluate_factors_end_to_end():
    data = _market(20, 200)
    res = evaluate_factors(data, ["mom_20", "vol_ratio", "atr_pct"], horizon=5)
    assert not res["summary"].empty
    assert set(res["quantiles"]) == {"mom_20", "vol_ratio", "atr_pct"}
    assert not res["coverage"].empty


def test_evaluate_factors_warns_on_short_sample():
    data = _market(20, 60)
    res = evaluate_factors(data, ["mom_20"], horizon=5)
    assert any("交易日" in w for w in res["warnings"])


def test_evaluate_factors_warns_about_dead_factors():
    data = _market(20, 200)      # 没有 yellow_line 列
    res = evaluate_factors(data, ["mom_20", "dist_yellow"], horizon=5)
    assert any("覆盖率" in w for w in res["warnings"])


# ---------------------------------------------------------------------------
# 预设组合与扫描器接线
# ---------------------------------------------------------------------------

def test_presets_only_reference_registered_factors():
    for key, meta in PRESETS.items():
        for name in meta["weights"]:
            assert name in FACTOR_REGISTRY, f"预设 {key} 引用了未注册的因子 {name}"


def test_preset_weights_unknown_returns_empty():
    assert preset_weights("根本没有") == {}


def test_ranking_weights_reads_by_strategy_first():
    from zhixing_quant.scanner._core import ranking_weights

    cfg = {"factors": {"ranking": "trend",
                       "by_strategy": {"b1": "pullback"}}}
    w_b1, label_b1 = ranking_weights(cfg, "b1")
    w_b2, _ = ranking_weights(cfg, "b2")
    assert "回踩" in label_b1
    assert w_b1 == preset_weights("pullback")
    assert w_b2 == preset_weights("trend"), "没单独指定的战法应回落到 ranking"


def test_ranking_weights_amount_means_no_factor_sort():
    from zhixing_quant.scanner._core import ranking_weights

    assert ranking_weights({"factors": {"ranking": "amount"}}, "b1") == ({}, "")
    assert ranking_weights({}, "b1") == ({}, "")


def test_ranking_weights_custom():
    from zhixing_quant.scanner._core import ranking_weights

    cfg = {"factors": {"ranking": "custom", "weights": {"mom_20": 2.0}}}
    w, label = ranking_weights(cfg, "b1")
    assert w == {"mom_20": 2.0} and "自定义" in label


def test_apply_factor_ranking_falls_back_on_failure():
    """排序出任何问题都必须退回成交额降序，不能把整轮扫描搞崩。"""
    from zhixing_quant.scanner._core import apply_factor_ranking

    df = pd.DataFrame({"code": ["600000", "600001"], "amount": [1e8, 5e8]})
    out, note, warn = apply_factor_ranking(
        df, {}, {"factors": {"ranking": "trend"}}, "b1", pd.Timestamp("2024-06-03"))
    assert list(out["code"]) == ["600001", "600000"], "应按成交额降序兜底"
    assert "成交额" in note and warn


def test_apply_factor_ranking_uses_scores_when_available():
    from zhixing_quant.scanner._core import apply_factor_ranking

    data = _market(8, 180)
    day = list(data.values())[0].index[-1]
    codes = list(data)
    df = pd.DataFrame({"code": codes,
                       "amount": np.linspace(1e8, 9e8, len(codes))})
    out, note, _ = apply_factor_ranking(
        df, data, {"factors": {"ranking": "custom",
                               "weights": {"mom_20": 1.0}}}, "b1", day)
    assert "因子" in note or "自定义" in note
    assert "score" in out.columns
    assert list(out["score"].dropna()) == sorted(out["score"].dropna(), reverse=True)


def test_shipped_config_factor_section_is_valid():
    from zhixing_quant.config import load_config
    from zhixing_quant.scanner._core import ranking_weights

    cfg = load_config()
    for strategy in ("b1", "b2", "brick"):
        w, label = ranking_weights(cfg, strategy)
        assert w, f"{strategy} 没有配到因子权重"
        for name in w:
            assert name in FACTOR_REGISTRY, f"{strategy} 的权重引用了未注册因子 {name}"


# ---------------------------------------------------------------------------
# 因子排序必须在**回测里**也生效
# ---------------------------------------------------------------------------

def test_engine_orders_same_day_signals_by_factor_score():
    """每日开仓数有上限，同一天命中多只时先买哪只由排序决定。

    这里曾经只按成交额排序，而因子排序只接在实盘扫描器里——改因子权重
    跑回测结果一个字节都不变，因子层完全无法被验证。又一次把回测和实盘
    劈成两套系统。
    """
    from zhixing_quant.backtest.engine import BacktestEngine

    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    prepared = {}
    for code, amount in (("600001", 9e8), ("600002", 1e8)):
        df = pd.DataFrame({"open": 10.0, "high": 10.5, "low": 9.5,
                           "close": 10.0, "amount": amount, "vol": 1e6,
                           "sig": True}, index=idx)
        prepared[code] = {
            "df": df, "pos": {ts: i for i, ts in enumerate(idx)},
            "open": df["open"].to_numpy(float), "high": df["high"].to_numpy(float),
            "low": df["low"].to_numpy(float), "close": df["close"].to_numpy(float),
            "sig": df["sig"].to_numpy(bool), "stop": df["low"].to_numpy(float),
        }
    eng = BacktestEngine({})
    day = idx[2]

    by_amount = [c for c, _ in eng._signals_on(prepared, day, "sig")]
    assert by_amount == ["600001", "600002"], "没有分数时按成交额降序"

    scores = {day: {"600001": -1.0, "600002": 2.0}}
    by_score = [c for c, _ in eng._signals_on(prepared, day, "sig", scores)]
    assert by_score == ["600002", "600001"], "有因子分时必须按分数排，不看成交额"


def test_engine_puts_unscored_candidates_last():
    """算不出因子分的不能冒到前面去。"""
    from zhixing_quant.backtest.engine import BacktestEngine

    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    prepared = {}
    for code in ("600001", "600002"):
        df = pd.DataFrame({"open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
                           "amount": 9e8, "vol": 1e6, "sig": True}, index=idx)
        prepared[code] = {
            "df": df, "pos": {ts: i for i, ts in enumerate(idx)},
            "open": df["open"].to_numpy(float), "high": df["high"].to_numpy(float),
            "low": df["low"].to_numpy(float), "close": df["close"].to_numpy(float),
            "sig": df["sig"].to_numpy(bool), "stop": df["low"].to_numpy(float),
        }
    day = idx[1]
    scores = {day: {"600002": -5.0}}          # 600001 没有分
    order = [c for c, _ in BacktestEngine({})._signals_on(
        prepared, day, "sig", scores)]
    assert order == ["600002", "600001"]


def test_build_rank_scores_returns_nothing_without_config():
    from zhixing_quant.backtest.runner import build_rank_scores

    scores, note = build_rank_scores({"factors": {"ranking": "amount"}},
                                     "b2", {})
    assert scores == {} and note == ""


def test_build_rank_scores_produces_per_day_maps():
    from zhixing_quant.backtest.runner import build_rank_scores

    data = _market(12, 200)
    cfg = {"factors": {"ranking": "custom", "weights": {"mom_20": 1.0}}}
    scores, note = build_rank_scores(cfg, "b2", data)
    assert scores, "应该算出分数"
    day = next(iter(scores))
    assert isinstance(scores[day], dict)
    assert set(scores[day]) <= set(data)
    assert "因子" in note or "自定义" in note


def test_build_rank_scores_degrades_gracefully():
    """算不出来时必须退回成交额排序，而不是把整个回测搞崩。"""
    from zhixing_quant.backtest.runner import build_rank_scores

    cfg = {"factors": {"ranking": "custom", "weights": {"mom_20": 1.0}}}
    scores, note = build_rank_scores(cfg, "b2", {})
    assert scores == {} and "成交额" in note


def test_runner_passes_rank_scores_to_the_engine():
    import inspect

    from zhixing_quant.backtest import runner

    src = inspect.getsource(runner.run_backtest)
    assert "rank_scores=rank_scores" in src


# ---------------------------------------------------------------------------
# 稳定性筛选：只留样本内外都过关的因子
# ---------------------------------------------------------------------------

def test_stable_weights_drop_in_sample_only_factors():
    """实测撞到过：atr_pct 样本内 ICIR +0.301(t=7.61)，样本外 +0.032(t=0.59)。
    全样本口径仍会把它排到第二位，必须靠两段都要求显著来挡掉。"""
    from zhixing_quant.factors.evaluate import stable_weights

    is_s = pd.DataFrame([{"因子": "good", "ICIR": 0.30, "t值": 8.0},
                         {"因子": "mirage", "ICIR": 0.30, "t值": 7.6}])
    oos_s = pd.DataFrame([{"因子": "good", "ICIR": 0.28, "t值": 5.0},
                          {"因子": "mirage", "ICIR": 0.03, "t值": 0.6}])
    w = stable_weights(is_s, oos_s)
    assert set(w) == {"good"}


def test_stable_weights_reject_sign_flips():
    from zhixing_quant.factors.evaluate import stable_weights

    is_s = pd.DataFrame([{"因子": "flip", "ICIR": 0.30, "t值": 8.0}])
    oos_s = pd.DataFrame([{"因子": "flip", "ICIR": -0.30, "t值": -8.0}])
    assert stable_weights(is_s, oos_s) == {}


def test_stable_weights_keep_consistently_reversed_factors():
    from zhixing_quant.factors.evaluate import stable_weights

    is_s = pd.DataFrame([{"因子": "rev", "ICIR": -0.26, "t值": -6.6}])
    oos_s = pd.DataFrame([{"因子": "rev", "ICIR": -0.45, "t值": -8.4}])
    assert stable_weights(is_s, oos_s)["rev"] < 0


def test_stability_report_labels_each_case():
    from zhixing_quant.factors.evaluate import stability_report

    is_s = pd.DataFrame([{"因子": "stable", "ICIR": 0.3, "t值": 8.0},
                         {"因子": "mirage", "ICIR": 0.3, "t值": 8.0},
                         {"因子": "noise", "ICIR": 0.01, "t值": 0.2}])
    oos_s = pd.DataFrame([{"因子": "stable", "ICIR": 0.28, "t值": 5.0},
                          {"因子": "mirage", "ICIR": 0.02, "t值": 0.4},
                          {"因子": "noise", "ICIR": 0.01, "t值": 0.3}])
    verdicts = dict(zip(*stability_report(is_s, oos_s)[["因子", "判定"]].values.T))
    assert verdicts["stable"] == "稳定"
    assert "幻觉" in verdicts["mirage"]
    assert verdicts["noise"] == "始终不显著"


# ---------------------------------------------------------------------------
# 战法命中人群上的条件 IC
# ---------------------------------------------------------------------------

def _with_signal(data: dict, hit_codes: set) -> dict:
    out = {}
    for code, df in data.items():
        d = df.copy()
        d["sig_x"] = code in hit_codes
        out[code] = d
    return out


def test_strategy_population_keeps_only_hits():
    from zhixing_quant.factors.evaluate import strategy_population
    data = _with_signal(_market(6, 40), {"600001", "600003"})
    pop = strategy_population(data, "sig_x")
    assert set(pop.index.get_level_values("code")) == {"600001", "600003"}
    assert bool(pop.all())
    assert len(pop) == 2 * 40


def test_strategy_population_drops_non_bull_days():
    from zhixing_quant.factors.evaluate import strategy_population
    data = _with_signal(_market(3, 20), {"600000"})
    dates = data["600000"].index
    regime = pd.Series(["BULL"] * 10 + ["BEAR"] * 10, index=dates)
    pop = strategy_population(data, "sig_x", regime=regime)
    assert set(pop.index.get_level_values("date")) == set(dates[:10])


def test_evaluate_factors_population_matches_manual_mask():
    """主入口：给了人群，IC 必须和「先筛面板再算 IC」逐位相同。"""
    from zhixing_quant.factors.evaluate import forward_returns, strategy_population
    hits = {f"{600000 + i:06d}" for i in range(0, 30, 2)}      # 一半的票
    data = _with_signal(_market(30, 200), hits)
    pop = strategy_population(data, "sig_x")

    res = evaluate_factors(data, ["mom_20"], horizon=5, population=pop)
    panel = build_panel(data, ["mom_20"])
    manual = factor_ic(panel[pop.reindex(panel.index, fill_value=False).to_numpy(bool)],
                       forward_returns(data, 5))
    pd.testing.assert_frame_equal(res["ic"], manual)
    assert any("人群" in w for w in res["warnings"])

    # 人群确实改变了结果：全市场 IC 和人群 IC 不是同一个东西
    full = evaluate_factors(data, ["mom_20"], horizon=5)
    assert not full["ic"]["mom_20"].equals(res["ic"]["mom_20"])


def test_evaluate_factors_empty_population_does_not_crash():
    from zhixing_quant.factors.evaluate import strategy_population
    data = _with_signal(_market(10, 100), set())
    res = evaluate_factors(data, ["mom_20"], population=strategy_population(data, "sig_x"))
    assert res["summary"].empty
    assert res["warnings"]


def test_regime_by_close_indexes_by_trade_date():
    from zhixing_quant.timing.active_value import regime_by_close
    states = pd.DataFrame({"trade_date": [20240102, 20240103],
                           "regime": ["BEAR", "BULL"]})
    s = regime_by_close(states=states)
    assert s[pd.Timestamp("2024-01-03")] == "BULL"
    assert s[pd.Timestamp("2024-01-02")] == "BEAR"
