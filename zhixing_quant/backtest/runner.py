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

from zhixing_quant.data.universe import UniverseSpec, build_universe

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
    from zhixing_quant.data.tdx_loader import load_daily, load_daily_many
    from zhixing_quant.indicators.pipeline import run_pipeline

    sig_col = BACKTESTABLE.get(strategy)
    if sig_col is None:
        raise ValueError(
            f"{strategy} 暂不支持回测。它的信号需要逐根K线求值，"
            f"而回测引擎读的是预先算好的信号列。目前支持：{', '.join(BACKTESTABLE)}"
        )

    warnings: List[str] = []
    as_of = universe_as_of or start
    if as_of > start:
        warnings.append(
            f"建池基准日 {as_of} 晚于回测开始日 {start}，结果含前视偏差，不可用于决策。"
        )

    uni = build_universe(cfg, as_of=as_of, spec=spec)
    if not uni.codes:
        raise ValueError(f"按 {as_of} 的条件筛不出任何标的，放宽建池条件试试。")

    # 指标需要预热，黄线含 MA114
    warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    raw = load_daily_many(uni.codes, start_date=warm, end_date=end)

    data: Dict[str, pd.DataFrame] = {}
    skipped = 0
    total = len(uni.codes)
    for i, code in enumerate(uni.codes, 1):
        df = raw.get(code)
        if df is None or len(df) < 130:
            skipped += 1
            continue
        try:
            data[code] = run_pipeline(df, cfg, strategy).df
        except Exception:
            skipped += 1
        if progress is not None and i % 25 == 0:
            progress(i, total)

    if not data:
        raise ValueError("池内标的的K线都不足 130 根，无法计算指标。")

    engine = BacktestEngine(cfg)
    result = engine.run(data, signal_col=sig_col, start_date=start, end_date=end)

    bench = _load_benchmark(benchmark_code, start, end, result.equity_curve)
    if bench is None:
        warnings.append(
            f"库里没有基准指数 {benchmark_code}，无法判断收益是超额还是跟着大盘涨。"
            "把指数加进 sync 的同步范围后重跑。"
        )

    return BacktestRun(
        metrics=result.metrics,
        equity_curve=result.equity_curve,
        trades=result.trades_frame(),
        benchmark=bench,
        universe_note=f"{uni.spec.describe()}｜{uni.summary()}",
        universe_codes=uni.codes,
        loaded=len(data),
        skipped=skipped,
        warnings=warnings,
    )


def _load_benchmark(code: str, start: str, end: str,
                    equity: pd.Series) -> Optional[pd.Series]:
    """加载基准指数并归一到与权益曲线同一起点。"""
    from zhixing_quant.data.tdx_loader import load_daily

    for candidate in (code, "sh000001", "000001"):
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
        return s
    return None


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
