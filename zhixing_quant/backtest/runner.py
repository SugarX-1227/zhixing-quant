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
    from zhixing_quant.indicators.pipeline import run_pipeline

    sig_col = BACKTESTABLE.get(strategy)
    if sig_col is None:
        raise ValueError(
            f"{strategy} 暂不支持回测。它的信号需要逐根K线求值，"
            f"而回测引擎读的是预先算好的信号列。目前支持：{', '.join(BACKTESTABLE)}"
        )

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
            data[code] = run_pipeline(df, cfg, strategy).df
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

    engine = BacktestEngine(cfg)
    result = engine.run(data, signal_col=sig_col, start_date=start, end_date=end)

    warnings.extend(survivorship_warnings(get_store()))

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
    )



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
