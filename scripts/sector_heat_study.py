"""板块热度研究：信号当天所在行业热不热，会不会影响之后的表现？

口径（事先定死）
----------------

- 行业：通达信 T 系一级行业（data/tdx_industry.csv，由 scripts/export_industry.py
  在数据机导出，GitHub Release industry-* 下发）。⚠ 这是导出当天的快照，
  拿它看过去有「用今天的行业归属看历史」的小偏差。
- 人群：多头区间、T+1 买得进的 sig_b2 / sig_b1 命中。
- 结果：T+1 开盘买到 T+5 收盘，减同日全市场等权平均（同 signal_event_study）。
  报按天平均、每笔平均、中位数三个数。
- 主要特征（T 日收盘可知）：
    行业涨停数      同一级行业当天涨停家数（不含自己）
    行业5日相对强度  行业等权 5 日涨幅 − 全市场等权 5 日涨幅（百分点）
  次要：行业B2数（同行业当天 B2 命中，不含自己）、行业当日涨幅。
- 分档阈值只用样本内定，样本外只检验。
- 附加：在「全市场命中数」最多的两档日子里，板块热度还有没有额外作用。

用法
----

    python scripts/sector_heat_study.py
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

from zhixing_quant.backtest.runner import collect_universe_pools  # noqa: E402
from zhixing_quant.config import load_config  # noqa: E402
from zhixing_quant.data.tdx_loader import load_daily_many  # noqa: E402
from zhixing_quant.indicators.pipeline import run_steps  # noqa: E402
from zhixing_quant.timing.active_value import regime_by_close  # noqa: E402

OOS = pd.Timestamp("2025-04-01")
H = 5


def log(*a) -> None:
    print(*a, flush=True)


def build(cfg: dict, industry: pd.Series, start: str, end: str) -> pd.DataFrame:
    """全市场股票日（紧凑）：行业、当日涨幅、涨停、5 日涨幅、信号、能否买进、5 日收益。"""
    _, codes, _ = collect_universe_pools(cfg, start, end)
    warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    raw = load_daily_many(codes, start_date=warm, end_date=end)
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    parts = []
    for code, df in raw.items():
        if df is None or len(df) < 150:
            continue
        d = run_steps(df, cfg, ["b1", "b2"]).df
        c, o1 = d["close"], d["open"].shift(-1)
        pct = (c / c.shift(1) - 1) * 100
        lim = 19.5 if str(code).startswith(("300", "301", "688", "689")) else 9.5
        fill = (~((o1 == d["high"].shift(-1)) & (o1 == d["low"].shift(-1))
                  & (o1 / c - 1 > 0.09)) & o1.notna())
        row = pd.DataFrame({
            "code": code, "hy": industry.get(code, "未知"),
            "pct": pct, "up_limit": pct >= lim, "r5back": c / c.shift(5) - 1,
            "b2": d["sig_b2"].fillna(False).astype(bool),
            "b1": d["sig_b1"].fillna(False).astype(bool),
            "fill": fill, "fwd": c.shift(-H) / o1 - 1,
        }, index=d.index)
        parts.append(row[(row.index >= lo) & (row.index <= hi)])
    ev = pd.concat(parts)
    ev.index.name = "date"
    ev = ev.reset_index()
    ev["bull"] = regime_by_close(cfg).reindex(ev["date"]).to_numpy() == "BULL"
    return ev


def attach_context(ev: pd.DataFrame) -> pd.DataFrame:
    base = ev[ev.fill].groupby("date")["fwd"].mean()
    ev["x5"] = ev["fwd"] - ev["date"].map(base)
    mkt5 = ev.groupby("date")["r5back"].mean()
    g = ev.groupby(["date", "hy"])
    ev["行业涨停数"] = g["up_limit"].transform("sum") - ev["up_limit"].astype(int)
    ev["行业B2数"] = g["b2"].transform("sum") - ev["b2"].astype(int)
    ev["行业当日涨幅"] = g["pct"].transform("mean")
    ev["行业5日相对强度"] = (g["r5back"].transform("mean") - ev["date"].map(mkt5)) * 100
    ev["全市场B2数"] = ev.groupby("date")["b2"].transform("sum")
    return ev


def bucket_table(hits: pd.DataFrame, feat: str, edges) -> pd.DataFrame:
    rows = []
    for part, sub in (("样本内", hits[hits.date < OOS]), ("样本外", hits[hits.date >= OOS])):
        b = np.digitize(sub[feat], edges)
        for i in range(len(edges) + 1):
            g = sub[b == i]
            if g.empty:
                continue
            daily = g.groupby("date")["x5"].mean()
            n = len(daily)
            rows.append({"区间": part, "档": f"Q{i + 1}", "笔数": len(g),
                         f"{feat}中位": round(float(g[feat].median()), 2),
                         "按天": f"{daily.mean():+.2%}", "每笔": f"{g.x5.mean():+.2%}",
                         "中位": f"{g.x5.median():+.2%}",
                         "t(按天)": round(daily.mean() / daily.std(ddof=1) * np.sqrt(n), 2) if n > 2 else None})
    return pd.DataFrame(rows)


def edges_from_is(hits: pd.DataFrame, feat: str) -> list:
    """样本内五分位切点；取值离散（如涨停数大多为 0）时去重，档数会少于 5。"""
    q = np.quantile(hits.loc[hits.date < OOS, feat].dropna(), [0.2, 0.4, 0.6, 0.8])
    return sorted(set(np.round(q, 4)))


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--industry", default="data/tdx_industry.csv")
    ap.add_argument("--start", default="20220801")
    ap.add_argument("--end", default="20260917")
    args = ap.parse_args()

    t0 = time.time()
    ind = pd.read_csv(args.industry, dtype=str, encoding="utf-8-sig")
    industry = ind.set_index("code")["hy_level1"]
    cfg = load_config()
    ev = attach_context(build(cfg, industry, args.start, args.end))
    cover = (ev.hy != "未知").mean()
    log(f"股票日 {len(ev):,}，行业覆盖 {cover:.1%}  [{time.time() - t0:.0f}s]")

    for kind in ("b2", "b1"):
        hits = ev[ev[kind] & ev.bull & ev.fill & (ev.hy != "未知")].dropna(subset=["x5"])
        log(f"\n################ {kind.upper()}：多头命中 {len(hits):,}")
        for feat in ("行业涨停数", "行业5日相对强度", "行业B2数", "行业当日涨幅"):
            e = edges_from_is(hits, feat)
            log(f"\n=== {feat}（样本内切点 {e}）===")
            log(bucket_table(hits, feat, e).to_string(index=False))
        if kind == "b2":
            e_m = np.quantile(hits.loc[hits.date < OOS].groupby("date")["全市场B2数"].first(), [0.6])
            hot = hits[hits["全市场B2数"] > e_m[0]]
            log(f"\n=== 附加：只看全市场 B2 数 > {int(e_m[0])} 的热门日（{len(hot):,} 笔），板块热度还有没有用 ===")
            for feat in ("行业涨停数", "行业5日相对强度"):
                log(f"--- {feat} ---")
                log(bucket_table(hot, feat, edges_from_is(hot, feat)).to_string(index=False))
    log(f"\n耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
