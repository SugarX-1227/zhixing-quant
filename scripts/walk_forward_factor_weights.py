"""因子权重的滚动前进验证：数据配出来的权重，能不能在「之后」的行情里赢过预设？

为什么要这个
------------

单次样本内外切分上，按条件 IC 配的 B2 权重样本内明显改善、样本外输给预设
（-7.0% vs +9.7%）。一种可能是因子方向随市场阶段变化，而那一刀恰好切在
两种市场之间。滚动前进每段只用**该段之前**的数据估权重，再在后面一段上
交易，是最接近实盘用法的检验：实盘里你也只能用过去的数据定权重。

规则（事先定死，不看结果再改）
------------------------------

- 训练段：多头区间下的战法命中人群上算条件 IC（和因子页同一个口径）。
- 训练段最后 horizon 个交易日不参与估计——它们的未来收益落在测试段里。
- 权重只喂训练段 IC（weights_from_ic），报告两个变体：前 6 个 / 全部显著。
- 训练段没有显著因子时沿用预设：没有证据就不改。
- 每个测试段独立回测（空仓起步），各段收益按复利串起来。
  跨段持仓被截断，这是近似；预设和权重变体受同样的截断，对比仍公平。

用法
----

    python scripts/walk_forward_factor_weights.py                 # B2，12 个月训练 / 6 个月测试
    python scripts/walk_forward_factor_weights.py --strategy b1 --test-months 3

必须用全量数据（data-20260918-all）：切片有选择偏差，而条件 IC 需要全市场
的横截面（B2 命中每天中位约 15~20 只，池子小了算不出 IC）。
全量跑一次 B2 约 80 分钟，大头是每个测试段的回测。
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import zhixing_quant.factors as F  # noqa: E402
from zhixing_quant.backtest.calibrate import apply_params, rolling_windows  # noqa: E402
from zhixing_quant.backtest.runner import (BACKTESTABLE,  # noqa: E402
                                           collect_universe_pools, run_backtest)
from zhixing_quant.config import load_config  # noqa: E402
from zhixing_quant.data.tdx_loader import load_daily_many  # noqa: E402
from zhixing_quant.factors.cross_section import build_panel  # noqa: E402
from zhixing_quant.factors.evaluate import (factor_ic, forward_returns,  # noqa: E402
                                            ic_summary, strategy_population,
                                            weights_from_ic)
from zhixing_quant.indicators.pipeline import PIPELINES, run_steps  # noqa: E402
from zhixing_quant.timing.active_value import regime_by_close  # noqa: E402

VARIANTS = {"前6个": 6, "全部显著": 99}


def log(*a) -> None:
    print(*a, flush=True)


def train_weights(panel: pd.DataFrame, fwd: pd.Series, pop: pd.Series,
                  lo: str, hi: str, horizon: int) -> dict:
    """训练段 [lo, hi] 上的条件 IC → 各变体的权重。"""
    dates = panel.index.get_level_values("date")
    in_win = (dates >= pd.Timestamp(lo)) & (dates <= pd.Timestamp(hi))
    keep = in_win & pop.reindex(panel.index, fill_value=False).to_numpy(bool)
    sub = panel[keep]
    # 隔离期：最后 horizon 个交易日的未来收益落进测试段，扔掉
    days = sorted(sub.index.get_level_values("date").unique())
    if len(days) > horizon:
        sub = sub[sub.index.get_level_values("date") <= days[-horizon - 1]]
    summary = ic_summary(factor_ic(sub, fwd))
    return {name: weights_from_ic(summary, max_factors=mf)
            for name, mf in VARIANTS.items()}, summary


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategy", default="b2", choices=sorted(BACKTESTABLE))
    ap.add_argument("--start", default="20220801")
    ap.add_argument("--end", default="20260917")
    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--test-months", type=int, default=6)
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()

    cfg = load_config()
    strat, sig_col = args.strategy, BACKTESTABLE[args.strategy]
    t0 = time.time()

    windows = rolling_windows(args.start, args.end, args.train_months, args.test_months)
    if not windows:
        sys.exit("区间放不下一个训练段 + 测试段，放宽 --start/--end。")
    log(f"{strat}：{len(windows)} 个窗口，训练 {args.train_months} 个月 / "
        f"测试 {args.test_months} 个月")

    # 因子与人群在完整历史上算一次，各训练段只切日期——没有预热期损失
    names = [f.name for f in F.list_factors()]
    steps = list(dict.fromkeys(F.required_steps(names) + PIPELINES.get(strat, [strat])))
    warm = (pd.Timestamp(args.start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    # 和回测同一套季度滚动池的并集，后面的训练段才不会缺掉新上市的票
    _, codes, _ = collect_universe_pools(cfg, args.start, args.end)
    raw = load_daily_many(codes, start_date=warm, end_date=args.end)
    data = {c: run_steps(df, cfg, steps).df
            for c, df in raw.items() if df is not None and len(df) > 150}
    panel = build_panel(data, names)
    fwd = forward_returns(data, horizon=args.horizon)
    pop = strategy_population(data, sig_col, regime=regime_by_close(cfg))
    log(f"面板 {len(panel):,} 行，{strat} 多头命中 {len(pop):,} 次  [{time.time()-t0:.0f}s]")

    rows, weight_log = [], []
    for tr, te in windows:
        weights, _ = train_weights(panel, fwd, pop, tr.start, tr.end, args.horizon)
        runs = {"预设": cfg}
        for name, w in weights.items():
            weight_log.append({"测试段": te.start, "变体": name,
                               "权重": ", ".join(f"{k}{v:+.2f}" for k, v in w.items())
                               or "（无显著因子，沿用预设）"})
            runs[name] = (apply_params(cfg, {f"factors.by_strategy.{strat}": "custom",
                                             "factors.weights": w}) if w else cfg)
        for name, c in runs.items():
            r = run_backtest(c, strat, te.start, te.end)
            m = r.metrics
            rows.append({"测试段": f"{te.start}~{te.end}", "方案": name,
                         "总收益": m["total_return"], "最大回撤": m["max_drawdown"],
                         "超额": r.excess_return, "笔数": m["total_trades"]})
        log(f"  测试段 {te.start}~{te.end} 完成  [{time.time()-t0:.0f}s]")

    log("\n=== 各测试段所用权重（只用该段之前的数据估计）===")
    log(pd.DataFrame(weight_log).to_string(index=False))

    t = pd.DataFrame(rows)
    log("\n=== 各测试段总收益 ===")
    log(t.pivot(index="测试段", columns="方案", values="总收益").round(3).to_string())
    log("\n=== 各测试段交易笔数 ===")
    log(t.pivot(index="测试段", columns="方案", values="笔数").to_string())

    log("\n=== 汇总 ===")
    summary = []
    for name, g in t.groupby("方案", sort=False):
        chained = float((1 + g["总收益"]).prod() - 1)
        summary.append({"方案": name, "串联总收益": round(chained, 3),
                        "赢预设的段数": None, "最差一段": round(float(g["总收益"].min()), 3),
                        "平均最大回撤": round(float(g["最大回撤"].mean()), 3),
                        "总笔数": int(g["笔数"].sum())})
    base = t[t["方案"] == "预设"].set_index("测试段")["总收益"]
    for row in summary:
        mine = t[t["方案"] == row["方案"]].set_index("测试段")["总收益"]
        row["赢预设的段数"] = f"{int((mine > base).sum())}/{len(base)}"
    log(pd.DataFrame(summary).to_string(index=False))
    log(f"\n耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
