"""回测执行器。

把原来写在 app.py 里的回测流程抽出来，好处有二：
1. 可以写测试。原来那段代码只能靠点界面验证。
2. 强制走时点正确的建池，界面上无法绕过。

关键修正：股票池必须按**回测开始日**（或滚动重建日）的数据选，
不能用最新快照。原实现 `filter_universe(fetch_a_spot(), cfg)` 取的是今天的
成交额排名，等于让 2024 年的策略知道 2026 年谁最活跃。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from zhixing_quant.data.universe import UniverseSpec, build_universe, rebalance_dates

# 回测引擎读的是预先算好的信号列，只有这几套战法能提供
BACKTESTABLE = {
    "brick": "sig_brick",
    "b1": "sig_b1",
    "b2": "sig_b2",
}


@dataclass
class BacktestRun:
    metrics: dict
    equity_curve: pd.Series
    trades: pd.DataFrame
    benchmark: Optional[pd.Series] = None
    universe_note: str = ""
    universe_codes: List[str] = field(default_factory=list)
    loaded: int = 0
    skipped: int = 0
    warnings: List[str] = field(default_factory=list)
    regime_log: pd.DataFrame = field(default_factory=pd.DataFrame)  # 区间触发日志
    exit_note: str = ""            # 本次实际生效的出场规则，界面要显示出来
    entry_note: str = ""           # 本次实际生效的建仓规则
    # 本次实际用的配置（含界面上的参数覆盖）。消融分析要在**同一份**配置上
    # 逐条关规则，拿全局配置去跑会把界面改过的参数丢掉，结论就不可比了。
    used_cfg: dict = field(default_factory=dict)

    @property
    def drawdown(self) -> pd.Series:
        if self.equity_curve.empty:
            return pd.Series(dtype=float)
        return self.equity_curve / self.equity_curve.cummax() - 1.0

    @property
    def excess_return(self) -> Optional[float]:
        """相对基准的超额收益。牛市里 40% 可能是跑输大盘的。"""
        if self.benchmark is None or self.benchmark.empty:
            return None
        bench = float(self.benchmark.iloc[-1] / self.benchmark.iloc[0] - 1.0)
        return float(self.metrics.get("total_return", 0.0)) - bench


def run_backtest(
    cfg: dict,
    strategy: str,
    start: str,
    end: str,
    spec: Optional[UniverseSpec] = None,
    universe_as_of: Optional[str] = None,
    benchmark_code: str = "sh000300",
    progress=None,
) -> BacktestRun:
    """跑一次回测。

    Args:
        cfg: 已经套用参数覆盖的配置。
        strategy: 战法名，必须在 BACKTESTABLE 里。
        start / end: YYYYMMDD。
        spec: 建池条件。
        universe_as_of: 建池基准日，默认取 start。**不要传 end 或 None**，
            那会引入前视偏差。
        benchmark_code: 基准指数，库里没有就退化为无基准。
        progress: 回调 (done, total)。

    Returns:
        BacktestRun
    """
    from zhixing_quant.backtest.engine import BacktestEngine
    from zhixing_quant.data.tdx_loader import (
        get_store, load_daily_many, survivorship_warnings,
    )
    from zhixing_quant.indicators.pipeline import PIPELINES, run_steps

    sig_col = BACKTESTABLE.get(strategy)
    if sig_col is None:
        raise ValueError(
            f"{strategy} 暂不支持回测。它的信号需要逐根K线求值，"
            f"而回测引擎读的是预先算好的信号列。目前支持：{', '.join(BACKTESTABLE)}"
        )

    # 出场规则可能要用战法流水线不产出的列（破黄线要 dual_line，
    # 防守阶梯要 sell_s / distribution）。缺列时 DefenseEngine 会一路
    # row.get(..., False)，规则静默失效且界面看不出来——所以这里
    # 把依赖并进流水线，而不是指望战法自己带上。
    from zhixing_quant.backtest.exits import required_steps, spec_from_config
    exit_spec = spec_from_config(cfg, strategy)
    steps = list(PIPELINES.get(strategy, [strategy]))
    for extra in required_steps(exit_spec):
        if extra not in steps:
            steps.append(extra)

    as_of = universe_as_of or start
    warnings: List[str] = check_universe_as_of(as_of, start)

    uni, codes, pools = collect_universe_pools(
        cfg, as_of, end, spec=spec, months=3,
    )
    warnings.extend(uni.warnings)
    if not codes:
        raise ValueError(f"按 {as_of} 的条件筛不出任何标的，放宽建池条件试试。")

    # 指标需要预热，黄线含 MA114
    warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    raw = load_daily_many(codes, start_date=warm, end_date=end)

    data: Dict[str, pd.DataFrame] = {}
    short_history = 0
    errors: Dict[str, str] = {}          # code -> 错误摘要，不再静默吞掉
    total = len(codes)
    for i, code in enumerate(codes, 1):
        df = raw.get(code)
        if df is None or len(df) < 130:
            short_history += 1
            continue
        try:
            data[code] = run_steps(df, cfg, steps).df
        except Exception as exc:
            errors[code] = f"{type(exc).__name__}: {exc}"
        if progress is not None and i % 25 == 0:
            progress(i, total)

    skipped = short_history + len(errors)

    if errors:
        # 按错误类型归并，避免 200 条一模一样的信息刷屏
        kinds: Dict[str, int] = {}
        for msg in errors.values():
            kinds[msg] = kinds.get(msg, 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[:3]
        detail = "；".join(f"{msg}（{n} 只）" for msg, n in top)
        warnings.append(f"{len(errors)} 只标的指标计算失败，已剔除：{detail}")

    if not data:
        raise ValueError(
            f"池内 {total} 只标的没有一只能算出指标。"
            f"K线不足 130 根 {short_history} 只，指标报错 {len(errors)} 只。"
            + (f" 首个错误：{next(iter(errors.values()))}" if errors else "")
        )

    data = mask_signals_by_membership(data, sig_col, pools, end)

    # 活跃市值区间（择时）：BEAR 日引擎会禁止开仓并在开盘清仓。
    # regime_before 把收盘态平移到次日，保证无未来函数。
    from zhixing_quant.timing.active_value import oamv_trigger_states, regime_before
    states = oamv_trigger_states(cfg)
    all_days = sorted({ts for frame in data.values() for ts in frame.index})
    regime_map = regime_before(all_days, states)

    # 出场规则：回测与实盘共用 config 的 exits 段，见 backtest/exits.py。
    # 不再是写死的「信号日低点止损 + 15% 止盈 + 满 20 日清仓」。
    from zhixing_quant.backtest.exits import policy_from_config
    exit_policy = policy_from_config(cfg, strategy)
    # 建仓规则：底仓比例 + 分批加仓（规划书 6.2.1 B1 五步循环第 1、3 步）
    from zhixing_quant.portfolio.sizer import entry_spec_from_config
    entry_spec = entry_spec_from_config(cfg, strategy)

    # 因子排序：同一天命中多只、每日开仓数有上限时，先买哪只由它决定。
    # 必须和实盘扫描器读同一份配置，否则因子层就成了只在实盘生效、
    # 回测里验证不了的摆设。
    rank_scores, rank_note = build_rank_scores(cfg, strategy, data)
    if rank_note:
        warnings.append(rank_note)

    engine = BacktestEngine(cfg)
    result = engine.run(data, signal_col=sig_col, start_date=start, end_date=end,
                        regime=regime_map, exit_policy=exit_policy,
                        entry_spec=entry_spec, rank_scores=rank_scores)

    warnings.extend(survivorship_warnings(get_store()))

    win = states[(states.trade_date >= int(start)) & (states.trade_date <= int(end))]
    n_bear = int((win.regime == "BEAR").sum())
    n_bull = int((win.regime == "BULL").sum())
    if n_bear:
        forced = result.metrics.get("bear_forced_exits", 0)
        warnings.append(
            f"活跃市值空头区间 {n_bear} 个交易日（多头 {n_bull} 日）："
            f"期间禁止开仓，强制清仓 {forced} 笔。"
        )
    regime_log = win.loc[win.trigger != "", ["trade_date", "close", "pct",
                                             "trigger", "regime"]]

    bench, bench_used = _load_benchmark(benchmark_code, start, end,
                                        result.equity_curve)
    if bench is None:
        warnings.append(
            f"库里没有基准指数 {benchmark_code}，也没有任何可用的备选指数，"
            "无法判断收益是超额还是跟着大盘涨。把指数加进 sync 的同步范围后重跑。"
        )
    elif bench_used != benchmark_code:
        warnings.append(
            f"库里没有 {benchmark_code}，已改用 {bench_used} 作基准。"
            f"下面的超额收益是相对 {bench_used} 算的，不是 {benchmark_code}。"
        )

    return BacktestRun(
        metrics=result.metrics,
        equity_curve=result.equity_curve,
        trades=result.trades_frame(),
        benchmark=bench,
        universe_note=(
            f"{uni.spec.describe()}｜滚动建池 {len(pools)} 次，合计 {len(codes)} 只"
        ),
        universe_codes=codes,
        loaded=len(data),
        skipped=skipped,
        warnings=warnings,
        regime_log=regime_log.reset_index(drop=True),
        exit_note=exit_spec.describe(),
        entry_note=entry_spec.describe(),
        used_cfg=cfg,
    )



def build_rank_scores(cfg: dict, strategy: str, data: Dict[str, pd.DataFrame]
                      ) -> tuple:
    """一次性算出整段区间的因子合成分，供引擎按日取用。

    逐日现算的话，几百只 × 上千个交易日会把回测拖垮；这里走
    `build_panel` + `score_panel` 的向量化路径，整段只算一次。

    Returns:
        ({日期: {代码: 分数}}, 说明文案)。没配因子排序时返回 ({}, "")，
        引擎退回按成交额降序。
    """
    from zhixing_quant.scanner._core import ranking_weights

    weights, label = ranking_weights(cfg, strategy)
    if not weights:
        return {}, ""
    try:
        from zhixing_quant.factors.cross_section import (build_panel, coverage,
                                                         score_panel)

        panel = build_panel(data, list(weights))
        if panel.empty:
            return {}, f"{label} 算不出因子面板，候选排序退回成交额降序。"
        cov = coverage(panel)
        dead = [n for n, v in cov.items() if v < 0.2]
        score = score_panel(panel, weights)
        score = score.dropna()
        if score.empty:
            return {}, f"{label} 一个因子都没算出有效值，候选排序退回成交额降序。"
        out: Dict[object, Dict[str, float]] = {}
        for (date, code), v in score.items():
            out.setdefault(date, {})[code] = float(v)
        note = f"候选排序：{label}（{len(weights)} 个因子）"
        if dead:
            note += f"；⚠ 覆盖率不足 20% 的因子：{'、'.join(dead)}，等于白给权重"
        return out, note
    except Exception as exc:
        return {}, (f"因子排序失败，候选排序退回成交额降序："
                    f"{type(exc).__name__}: {exc}")


def collect_universe_pools(
    cfg: dict,
    start: str,
    end: str,
    spec: Optional[UniverseSpec] = None,
    months: int = 3,
) -> tuple:
    """按季度（默认）滚动建池。months=0 则只在 start 建一次。"""
    dates = rebalance_dates(start, end, months=months) if months else [str(start)]
    pools = []
    codes: List[str] = []
    seen = set()
    last = None
    for d in dates:
        last = build_universe(cfg, as_of=d, spec=spec)
        pools.append((d, set(last.codes)))
        for code in last.codes:
            if code not in seen:
                seen.add(code)
                codes.append(code)
    if last is None:
        raise ValueError(f"按 {start} 的条件筛不出任何标的，放宽建池条件试试。")
    return last, codes, pools


def mask_signals_by_membership(
    data: Dict[str, pd.DataFrame],
    sig_col: str,
    pools: List[tuple],
    end: str,
) -> Dict[str, pd.DataFrame]:
    """池外日期的买入信号关掉。已有持仓仍由引擎按止损/止盈处理。"""
    if not pools or len(pools) == 1:
        return data
    bounds = []
    for i, (d, members) in enumerate(pools):
        lo = pd.Timestamp(d)
        hi = (pd.Timestamp(pools[i + 1][0]) if i + 1 < len(pools)
              else pd.Timestamp(end) + pd.Timedelta(days=1))
        bounds.append((lo, hi, members))
    out: Dict[str, pd.DataFrame] = {}
    for code, df in data.items():
        if sig_col not in df.columns:
            out[code] = df
            continue
        eligible = pd.Series(False, index=df.index)
        for lo, hi, members in bounds:
            if code in members:
                eligible |= (df.index >= lo) & (df.index < hi)
        if bool(eligible.all()):
            out[code] = df
            continue
        copied = df.copy()
        copied.loc[~eligible, sig_col] = False
        out[code] = copied
    return out


def check_universe_as_of(as_of: str, start: str) -> List[str]:
    """建池基准日不得晚于回测开始日。

    抽成纯函数是为了能测。这条判据一旦失效，回测收益会被系统性抬高，
    而且抬多少无法估计——不会有任何测试因此变红。

    Args:
        as_of / start: YYYYMMDD 字符串（定长，可直接字典序比较）。

    Returns:
        告警列表，为空表示时点正确。
    """
    if str(as_of) > str(start):
        return [f"建池基准日 {as_of} 晚于回测开始日 {start}，"
                f"等于让开始那天就知道后面谁最活跃。结果含前视偏差，不可用于决策。"]
    return []


# 允许的基准候选。顺序即优先级，全是指数代码，不含任何个股。
BENCHMARK_FALLBACKS = ("sh000300", "sh000905", "sh000001", "sz399001")


def _load_benchmark(code: str, start: str, end: str,
                    equity: pd.Series) -> tuple[Optional[pd.Series], Optional[str]]:
    """加载基准指数并归一到与权益曲线同一起点。

    ⚠️ 原实现的候选链是 `(code, "sh000001", "000001")`，有两个问题：

    1. 请求 sh000300 而库里没有时，会**静默**换成上证指数返回。调用方那边
       只在 bench 为 None 时才告警，而 fallback 保证了它几乎不会是 None——
       于是界面上照常画出一条"基准"曲线和一个超额收益数字，
       而你不知道自己比的到底是沪深300还是上证。
    2. 最后那个 `"000001"` 是 6 位代码，在本地库里是**平安银行**
       （上证指数存的是 sh000001，见 data/sync.py 的注释）。也就是说
       最坏情况下会拿一只银行股的股价当大盘基准算超额收益。

    现在候选链只含指数，并且把实际用到的代码回传给调用方去告警。

    Returns:
        (归一化后的基准序列, 实际使用的代码)。两者都可能为 None。
    """
    from zhixing_quant.data.tdx_loader import load_daily

    candidates = [code] + [c for c in BENCHMARK_FALLBACKS if c != code]
    for candidate in candidates:
        try:
            df = load_daily(candidate, start_date=start, end_date=end, adjust="")
        except Exception:
            continue
        if df.empty or len(df) < 2:
            continue
        s = df["close"]
        if equity is not None and not equity.empty:
            s = s.reindex(equity.index, method="ffill").dropna()
            if s.empty:
                continue
            s = s / s.iloc[0] * float(equity.iloc[0])
        return s, candidate
    return None, None


def compare_strategies(cfg: dict, strategies: List[str], start: str, end: str,
                       spec: Optional[UniverseSpec] = None) -> pd.DataFrame:
    """同一股票池、同一区间跑多套战法，横向对比。"""
    rows = []
    for name in strategies:
        if name not in BACKTESTABLE:
            continue
        try:
            r = run_backtest(cfg, name, start, end, spec=spec)
        except Exception as exc:
            rows.append({"战法": name, "错误": str(exc)[:60]})
            continue
        m = r.metrics
        rows.append({
            "战法": name,
            "总收益": m.get("total_return", 0),
            "年化": m.get("annualized_return", 0),
            "最大回撤": m.get("max_drawdown", 0),
            "夏普": m.get("sharpe", 0),
            "胜率": m.get("win_rate", 0),
            "盈亏比": m.get("profit_loss_ratio", 0),
            "笔数": m.get("total_trades", 0),
            "超额": r.excess_return,
        })
    return pd.DataFrame(rows)
