"""信号事件研究：战法命中之后，到底有没有跑赢同一天随便买一只？

为什么要这个
------------

回测收益混着出场规则、仓位、排序、择时，好坏说不清是谁的功劳。
事件研究只回答最底层的问题：**信号本身有没有正期望**。信号是负期望时，
出场、排序、仓位都只是在一个亏钱的东西上挪动结果——B2 就是这样，
调了几十轮出场和因子权重都没用，事件研究 4 分钟就给出了原因。

口径
----

- 超额 = 命中标的的收益 − 同一天全市场（买得进的）等权平均收益。
  B2 命中的**原始**5 日收益 +2.3%，看着很好；但命中扎堆在大盘强势的日子，
  减掉同日大盘后是 -0.51%。只看原始收益会被大盘骗。
- 买入：T+1 开盘（与回测一致）；另报 T 收盘买入（尾盘口径，决策用了 T 日
  收盘价，是上限不是可成交价）。T+1 一字涨停买不进，剔除。
- t 值先按日平均再跨日算：同一天的多个命中高度相关，不能当独立样本。
- 往返成本约 0.45%（滑点 0.15%×2 + 佣金 + 印花税 + 过户费），
  超额低于它 = 扣完成本还不如随便买一只。

用法
----

    python scripts/signal_event_study.py                  # B2
    python scripts/signal_event_study.py --strategy b1
    python scripts/signal_event_study.py --strategy brick --all-regimes

必须用全量数据（data-20260918-all），切片有选择偏差。全量一次约 4 分钟。
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from zhixing_quant.backtest.runner import (BACKTESTABLE,  # noqa: E402
                                           collect_universe_pools)
from zhixing_quant.config import load_config  # noqa: E402
from zhixing_quant.data.tdx_loader import load_daily_many  # noqa: E402
from zhixing_quant.indicators.pipeline import PIPELINES, run_steps  # noqa: E402
from zhixing_quant.timing.active_value import regime_by_close  # noqa: E402

HORIZONS = (1, 3, 5, 10, 20)
COST = 0.0045


def log(*a) -> None:
    print(*a, flush=True)


def build_events(cfg: dict, strategy: str, start: str, end: str) -> pd.DataFrame:
    """全市场股票日：信号、能否买进、各持有期收益、当日区间、减同日均值后的超额。"""
    sig_col = BACKTESTABLE[strategy]
    steps = PIPELINES.get(strategy, [strategy])
    _, codes, _ = collect_universe_pools(cfg, start, end)
    warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    raw = load_daily_many(codes, start_date=warm, end_date=end)

    parts = []
    for code, df in raw.items():
        if df is None or len(df) < 150:
            continue
        d = run_steps(df, cfg, steps).df
        o, c = d["open"], d["close"]
        o1 = o.shift(-1)
        blocked = ((o1 == d["high"].shift(-1)) & (o1 == d["low"].shift(-1))
                   & (o1 / c - 1 > 0.09))
        row = pd.DataFrame({"sig": d[sig_col].fillna(False).astype(bool),
                            "fill": ~blocked & o1.notna()}, index=d.index)
        for h in HORIZONS:
            row[f"oc{h}"] = c.shift(-h) / o1 - 1.0      # T+1 开盘买
            row[f"cc{h}"] = c.shift(-h) / c - 1.0       # T 收盘买（上限）
        parts.append(row[(row.index >= pd.Timestamp(start))
                         & (row.index <= pd.Timestamp(end))])
    ev = pd.concat(parts)
    ev.index.name = "date"
    ev["bull"] = regime_by_close(cfg).reindex(ev.index).to_numpy() == "BULL"
    ev = ev.reset_index()

    cols = [c for c in ev.columns if c[:2] in ("oc", "cc")]
    base = ev[ev.fill].groupby("date")[cols].mean()
    for col in cols:
        ev["x_" + col] = ev[col] - ev["date"].map(base[col])
    return ev


def stat(ev: pd.DataFrame, mask: pd.Series, col: str, label: str) -> dict:
    s = ev[mask & ev.fill].dropna(subset=["x_" + col])
    if s.empty:
        return {"": label, "事件数": 0}
    daily = s.groupby("date")["x_" + col].mean()
    n = len(daily)
    t = daily.mean() / daily.std(ddof=1) * np.sqrt(n) if n > 2 else np.nan
    return {"": label, "事件数": len(s), "天数": n, "原始收益": s[col].mean(),
            "超额": daily.mean(), "t值": t, "跑赢比例": (s["x_" + col] > 0).mean()}


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategy", default="b2", choices=sorted(BACKTESTABLE))
    ap.add_argument("--start", default="20220801")
    ap.add_argument("--end", default="20260917")
    ap.add_argument("--oos-start", default="20250401")
    ap.add_argument("--all-regimes", action="store_true",
                    help="不只看多头区间（超短战法可能不看大盘）")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()
    ev = build_events(cfg, args.strategy, args.start, args.end)
    hit = ev.sig if args.all_regimes else ev.sig & ev.bull
    scope = "全部区间" if args.all_regimes else "多头区间"
    log(f"{args.strategy}：股票日 {len(ev):,}，命中 {int(ev.sig.sum()):,}，"
        f"{scope}命中 {int(hit.sum()):,}  [{time.time()-t0:.0f}s]")

    fmt = lambda rows: pd.DataFrame(rows).round(4).to_string(index=False)  # noqa: E731
    log(f"\n=== 各持有期（{scope}，T+1 开盘买，超额 = 减同日全市场等权）===")
    log(fmt([stat(ev, hit, f"oc{h}", f"{h} 日") for h in HORIZONS]))
    log(f"往返成本约 {COST:.2%}：超额低于它 = 扣完成本还不如随便买一只")

    log("\n=== 买点：T 收盘买（上限）vs T+1 开盘买，5 日 ===")
    log(fmt([stat(ev, hit, "cc5", "T 收盘买"), stat(ev, hit, "oc5", "T+1 开盘买")]))

    log("\n=== 择时：多头 vs 空头，5 日 ===")
    log(fmt([stat(ev, ev.sig & ev.bull, "oc5", "多头"),
             stat(ev, ev.sig & ~ev.bull, "oc5", "空头")]))

    log("\n=== 稳定性：逐年 / 样本内外，5 日 ===")
    yr = ev["date"].dt.year
    oos = pd.Timestamp(args.oos_start)
    rows = [stat(ev, hit & (yr == y), "oc5", str(y)) for y in sorted(yr.unique())]
    rows += [stat(ev, hit & (ev["date"] < oos), "oc5", f"样本内 <{args.oos_start}"),
             stat(ev, hit & (ev["date"] >= oos), "oc5", f"样本外 ≥{args.oos_start}")]
    log(fmt(rows))
    log(f"\n耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
