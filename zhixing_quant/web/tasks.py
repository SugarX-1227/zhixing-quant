"""后台任务的具体内容。每个函数接收 (job, cfg, 参数)，返回给前端的 JSON。

逻辑逐段对应原 Streamlit 页面，只换了输入输出的形状：参数从 JSON 来，
结果转成 JSON 回去；K线、回测对象这类体积大或后续要复用的东西放 job.private。
"""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from typing import Dict, List, Optional

import pandas as pd

from zhixing_quant.ui import param_schema as PS
from zhixing_quant.web import views
from zhixing_quant.web.serialize import frame, jsonable, records, scalar, series


def ymd(s: str) -> str:
    """'2026-09-17' / '20260917' → '20260917'"""
    return str(s).replace("-", "")[:8]


def coerce(overrides: Dict, params: List[PS.Param]) -> Dict:
    """只接受声明过的参数，并按声明的类型转换。

    JSON 里 5 和 5.0 不分，写进配置后 int 参数变成 float 会让下游
    range()/iloc 之类直接报错；没声明的键一律丢掉，界面不能借此改任意配置。
    """
    kinds = {p.key: p for p in params}
    out = {}
    for key, value in (overrides or {}).items():
        p = kinds.get(key)
        if p is None or value is None:
            continue
        if p.kind == "int":
            out[key] = int(round(float(value)))
        elif p.kind == "bool":
            out[key] = bool(value)
        elif p.kind == "choice":
            if value not in p.choices:
                raise ValueError(f"{p.label} 不接受取值 {value!r}")
            out[key] = value
        else:
            out[key] = float(value)
    return out


def universe_spec(u: Dict):
    from zhixing_quant.data.universe import UniverseSpec

    return UniverseSpec(
        boards=tuple(u.get("boards") or ("MAIN",)),
        size=int(u.get("size", 200)),
        min_amount=float(u.get("min_amount", 1.0)) * 1e8,
        exclude_st=bool(u.get("exclude_st", True)),
        min_listed_bars=int(u.get("min_bars", 120)),
        rank_by=u.get("rank_by", "amount"),
    )


# ---------------------------------------------------------------------------
# 今日 / 战法
# ---------------------------------------------------------------------------

def today(job, cfg, p: Dict) -> dict:
    from zhixing_quant.executor.daily_workflow import run_daily_cycle

    result = run_daily_cycle(
        cfg, date=ymd(p["date"]), book=p.get("book", "swing"),
        strategy_key=p["strategy"], limit_universe=int(p.get("limit", 500)),
        progress=lambda d, t: job.report(d, t, f"扫描 {d}/{t}"))
    job.private["charts"] = result.charts
    return views.daily(result)


def scan(job, cfg, p: Dict) -> dict:
    from zhixing_quant.scanner.strategy_scan import scan_strategy

    name = p["strategy"]
    overrides = coerce(p.get("overrides"), PS.params_for(name))
    local = PS.apply_overrides(cfg, overrides)
    local.setdefault("universe", {})["max_candidates"] = 9999   # 不截断
    limit = int(p.get("limit", 500))
    cands, charts = scan_strategy(
        name, local, end_date=ymd(p["date"]), limit_universe=limit,
        progress=lambda d, t: job.report(d, t, f"扫描 {d}/{t}"))
    job.private["charts"] = charts
    return {"strategy": name, "scanned": limit,
            "changed": jsonable(PS.diff_from_default(overrides)),
            **views.candidates(cands)}


# ---------------------------------------------------------------------------
# 回测 / 消融
# ---------------------------------------------------------------------------

def backtest_params(name: str, cfg: dict) -> List[PS.Param]:
    return (PS.params_for(name) + PS.exit_params_for(name, cfg)
            + PS.EXECUTION_PARAMS + PS.COST_PARAMS + PS.RISK_PARAMS)


def backtest(job, cfg, p: Dict) -> dict:
    from zhixing_quant.backtest.ablation import active_switches
    from zhixing_quant.backtest.runner import TIMING_ONLY, run_backtest

    name = p["strategy"]
    overrides = coerce(p.get("overrides"), backtest_params(name, cfg))
    core = str(p.get("core_code") or "")
    local = PS.apply_overrides(cfg, {**overrides, "backtest.core.code": core})
    start, end = ymd(p["start"]), ymd(p["end"])
    spec = universe_spec(p.get("universe") or {})
    job.text = "准备数据"
    run = run_backtest(local, name, start, end, spec=spec, universe_as_of=start,
                       progress=lambda d, t: job.report(d, t, f"计算指标 {d}/{t}"))
    # 消融要在**本次实际生效**的配置上逐条关规则，这里留着给消融任务用
    job.private.update(run=run, strategy=name, start=start, end=end, spec=spec)
    n_sw = None
    if name != TIMING_ONLY:
        try:
            n_sw = len(active_switches(run.used_cfg or local, name))
        except Exception:
            n_sw = None
    changed = PS.diff_from_default(overrides, PS.exit_params_for(name, cfg))
    return views.backtest(run, name, changed, n_sw)


def ablation(job, cfg, p: Dict, source) -> dict:
    """source: 已完成的回测任务。"""
    from zhixing_quant.backtest.ablation import ablate_exits, summarize

    src = source.private
    table = ablate_exits(
        src["run"].used_cfg or cfg, src["strategy"], src["start"], src["end"],
        spec=src["spec"], universe_as_of=src["start"],
        only_one=bool(p.get("only_one")),
        progress=lambda d, t, n: job.report(d, t, f"{d}/{t} {n}"))
    if table is None or table.empty:
        return {"summary": [], "table": frame(pd.DataFrame())}
    view = table[["规则", "总收益", "最大回撤", "夏普", "胜率", "笔数",
                  "Δ总收益", "Δ最大回撤", "Δ笔数", "判定"]]
    return {"summary": summarize(table), "table": frame(view),
            "only_one": bool(p.get("only_one"))}


# ---------------------------------------------------------------------------
# 因子
# ---------------------------------------------------------------------------

def factors(job, cfg, p: Dict) -> dict:
    import zhixing_quant.factors as FA
    from zhixing_quant.backtest.runner import BACKTESTABLE
    from zhixing_quant.data.tdx_loader import load_daily_many
    from zhixing_quant.data.universe import build_universe
    from zhixing_quant.factors.evaluate import evaluate_factors, monotonicity
    from zhixing_quant.factors.library import PRESETS
    from zhixing_quant.indicators.pipeline import PIPELINES, run_steps

    picked = list(p.get("factors") or [])
    if not picked:
        raise ValueError("至少选一个因子。")
    start, end = ymd(p["start"]), ymd(p["end"])
    pop_key = p.get("population", "all")
    horizon, q = int(p.get("horizon", 5)), int(p.get("quantiles", 5))

    job.report(0, 1, "建池")
    spec = universe_spec({"boards": ("MAIN", "CHINEXT"), "size": p.get("size", 200),
                          "min_amount": p.get("min_amount", 1.0),
                          "exclude_st": True, "min_bars": 120})
    uni = build_universe(cfg, as_of=start, spec=spec)
    warm = (pd.Timestamp(start) - timedelta(days=400)).strftime("%Y%m%d")
    job.report(0.2, 1, f"加载 {len(uni.codes)} 只行情")
    raw = load_daily_many(uni.codes, start_date=warm, end_date=end)
    steps = FA.required_steps(picked)
    if pop_key != "all":
        steps = list(dict.fromkeys(steps + PIPELINES.get(pop_key, [pop_key])))
    job.report(0.5, 1, f"计算指标（{', '.join(steps) or '无'}）")
    data = {c: run_steps(df, cfg, steps).df
            for c, df in raw.items() if df is not None and len(df) > 150}
    population = None
    if pop_key != "all":
        from zhixing_quant.factors.evaluate import strategy_population
        from zhixing_quant.timing.active_value import regime_by_close
        regime = regime_by_close(cfg) if p.get("bull_only", True) else None
        population = strategy_population(data, BACKTESTABLE[pop_key], regime)
    job.report(0.8, 1, "计算 IC 与分层收益")
    res = evaluate_factors(data, picked, horizon=horizon, q=q, start=start, end=end,
                           population=population)
    summary = res["summary"]
    job.private.update(summary=summary, population=pop_key)

    out = {"warnings": list(res["warnings"]), "n_loaded": len(data),
           "n_days": len(res["ic"]), "population": pop_key,
           "summary": frame(summary), "presets": [], "quantiles": {}, "ic_cum": {},
           "coverage": []}
    if summary.empty:
        return out

    out["flipped"] = list(summary.loc[summary["方向"] == "⚠ 相反", "因子"])
    out["any_agreed"] = bool((summary["方向"] == "一致").any())

    # 预设组合在实测 IC 下的净方向——预设是拍脑袋给的，必须拿数据打分
    icir_of = dict(zip(summary["因子"], summary["ICIR"]))
    rows = []
    for key, meta in PRESETS.items():
        w = {k: v for k, v in meta["weights"].items() if k in icir_of}
        if not w:
            continue
        tot = sum(abs(v) for v in w.values()) or 1.0
        rows.append({"预设组合": f"{meta['label']}（{key}）",
                     "加权净ICIR": round(sum(icir_of[k] * v for k, v in w.items()) / tot, 3),
                     "反向因子数": sum(1 for k, v in w.items() if icir_of[k] * v < 0),
                     "因子数": len(w)})
    if rows:
        out["presets"] = records(pd.DataFrame(rows).sort_values("加权净ICIR",
                                                                ascending=False))

    for name, qret in res["quantiles"].items():
        if qret is None or qret.empty:
            continue
        out["quantiles"][name] = {"table": frame(qret, index=True),
                                  "mono": scalar(monotonicity(qret)),
                                  "bars": series(qret["平均未来收益"])}
    ic = res["ic"]
    for name in ic.columns:
        out["ic_cum"][name] = series(ic[name].dropna().cumsum())
    out["coverage"] = [[str(k), scalar(v)] for k, v in res["coverage"].head(3).items()]
    return out


def factor_weights(source, p: Dict) -> dict:
    from zhixing_quant.factors.evaluate import weights_as_yaml, weights_from_ic

    gen = weights_from_ic(source.private["summary"],
                          max_factors=int(p.get("max_factors", 6)),
                          min_abs_t=float(p.get("min_t", 2.0)),
                          allow_flip=bool(p.get("allow_flip", True)))
    return {"weights": jsonable(gen), "yaml": weights_as_yaml(gen, p.get("target", "b2"))}


# ---------------------------------------------------------------------------
# 校准
# ---------------------------------------------------------------------------

def _calib_common(p: Dict):
    spec = universe_spec({"boards": ("MAIN", "CHINEXT"), "size": p.get("size", 150),
                          "min_amount": p.get("min_amount", 1.0),
                          "exclude_st": True, "min_bars": 120})
    allowed = calib_params(p["strategy"])
    grid = {k: [x for x in v if x in allowed[k][1]]
            for k, v in (p.get("grid") or {}).items() if k in allowed}
    grid = {k: v for k, v in grid.items() if v}
    if not grid:
        raise ValueError("至少选一个参数和一个取值。")
    return spec, grid


def sweep(job, cfg, p: Dict) -> dict:
    from zhixing_quant.backtest.calibrate import plateau_score, sweep as run_sweep

    spec, grid = _calib_common(p)
    res = run_sweep(cfg, p["strategy"], grid, ymd(p["start"]), ymd(p["end"]), spec=spec,
                    oos_frac=float(p.get("oos", 0.3)), top_n=int(p.get("top_n", 3)),
                    objective_kind=p.get("objective", "calmar"),
                    progress=lambda d, t, s: job.report(d, t, f"{d}/{t} {s}"))
    flat = []
    if not res.table.empty:
        for path in grid:
            col = ".".join(path.split(".")[-2:])
            sc = plateau_score(res.table, col)
            if sc:
                flat.append({"参数": col, "平坦度": round(sc, 3),
                             "判定": "平台，稳健" if sc >= 0.5 else "尖峰，换段数据就会塌"})
    return {
        "warnings": [{"text": w, "level": "error" if ("⚠️" in w or "不该上实盘" in w)
                      else "info"} for w in res.warnings],
        "table": frame(res.table), "flatness": flat,
    }


def walkforward(job, cfg, p: Dict) -> dict:
    from zhixing_quant.backtest.calibrate import walk_forward, walk_forward_summary

    spec, grid = _calib_common(p)
    wf = walk_forward(cfg, p["strategy"], grid, ymd(p["start"]), ymd(p["end"]), spec=spec,
                      train_months=int(p.get("train_months", 12)),
                      test_months=int(p.get("test_months", 3)),
                      objective_kind=p.get("objective", "calmar"),
                      progress=lambda d, t, s: job.report(d, t, f"{d}/{t} {s}"))
    lines = walk_forward_summary(wf)
    return {
        "summary": [{"text": s, "level": "error" if ("不要拿去实盘" in s or "噪声" in s)
                     else "info"} for s in lines],
        "table": frame(wf),
    }


# ---------------------------------------------------------------------------
# 数据同步
# ---------------------------------------------------------------------------

def sync(job, cfg, p: Dict) -> dict:
    from zhixing_quant.data.tdx_loader import reset_store

    cmd = [sys.executable, "-m", "zhixing_quant.data.sync"]
    for flag in ("full", "names", "xdxr", "oamv"):
        if p.get(flag):
            cmd.append(f"--{flag}")
    job.text = "同步中"
    proc = subprocess.run(cmd, capture_output=True, text=True)
    reset_store()
    # K 线同步成功但活跃市值/除权等后续步骤失败时，报错在 stderr，只显示 stdout 会把它吞掉
    return {"cmd": " ".join(cmd[1:]), "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


RUNNERS = {"today": today, "scan": scan, "backtest": backtest, "factors": factors,
           "sweep": sweep, "walkforward": walkforward, "sync": sync}


def calib_params(strategy: Optional[str] = None) -> Dict:
    """校准页可选的参数：路径 → (中文名, 建议取值)。"""
    items = {
        "exits.{s}.stop.kind": ("止损方式", ["entry_low", "pct", "atr"]),
        "exits.{s}.stop.pct": ("止损百分比", [0.04, 0.06, 0.08, 0.10]),
        "exits.{s}.stop.atr_mult": ("止损ATR倍数", [1.5, 2.0, 2.5, 3.0]),
        "exits.{s}.trailing.kind": ("移动止损", ["none", "pct", "chandelier",
                                                "white_line", "yellow_line"]),
        "exits.{s}.trailing.pct": ("移动止损回撤", [0.06, 0.10, 0.15]),
        "exits.{s}.trailing.activate_profit": ("移动止损启用浮盈", [0.0, 0.05, 0.10]),
        "exits.{s}.take_profit.pct": ("止盈百分比", [0.10, 0.15, 0.25]),
        "exits.{s}.time_stop.max_holding_days": ("最长持有", [0, 5, 10, 20, 30]),
        "exits.{s}.time_stop.no_progress_days": ("N日不拉升", [0, 2, 3, 5]),
        "exits.{s}.profit_to_loss": ("盈转亏门槛", [0.0, 0.02, 0.05]),
        "entries.{s}.base_pct": ("底仓比例", [0.10, 0.15, 0.20]),
        "entries.{s}.max_addons": ("最多加仓次数", [0, 2, 4]),
    }
    return items if strategy is None else {k.format(s=strategy): v for k, v in items.items()}
