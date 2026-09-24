"""择时基准：只用活跃市值择时、不选股，多头区间持有指数或一篮子，空头空仓。

回答的问题：这套体系里真正赚钱的是择时还是选股？如果「择时 + 随便持有
指数」已经跑赢「择时 + B1/B2 选股」，选股这一层就没有贡献正收益。

口径（事先定死，择时阈值用课程原值，不做任何调参）
--------------------------------------------------

- 区间：T 日收盘后的活跃市值区间（timing.active_value，−2.3% 转空 / +4% 转多）。
- 执行：T 日收盘判定，T+1 开盘调仓。收益按「开盘到次日开盘」计，
  持仓 = 前一日收盘后的区间是否为多头。
- 标的：库里的 6 个指数（可用 ETF 实际买到）+ 全市场等权（每日再平衡，
  是「随便买一篮子」的理想化上限：没算停牌与涨停买不进）。
- 成本：每次进出扣单边成本。指数按往返 0.15%（ETF 佣金 + 滑点），
  等权篮子按往返 0.45%（与个股回测相同）。
- 报买入持有 / 择时持有两种，并拆样本内外（2025-04-01 为界）。

用法
----

    python scripts/timing_baseline.py
    python scripts/timing_baseline.py --start 20210701
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from zhixing_quant.backtest.runner import collect_universe_pools  # noqa: E402
from zhixing_quant.config import load_config  # noqa: E402
from zhixing_quant.data.tdx_loader import load_daily_many  # noqa: E402
from zhixing_quant.timing.active_value import regime_by_close  # noqa: E402

INDICES = {"sh000300": "沪深300", "sh000905": "中证500", "sh000852": "中证1000",
           "sz399006": "创业板指", "sh000001": "上证指数", "sz399001": "深证成指"}
OOS = pd.Timestamp("2025-04-01")


def open_to_open(opens: pd.Series) -> pd.Series:
    """第 t 天持有「从 t 日开盘到 t+1 日开盘」的收益。"""
    return opens.shift(-1) / opens - 1.0


def run(ret: pd.Series, pos: pd.Series, one_way_cost: float) -> pd.Series:
    """每日净收益：持仓 × 当日收益，仓位变化那天扣单边成本。"""
    pos = pos.reindex(ret.index).fillna(0.0)
    turn = pos.diff().abs().fillna(pos.iloc[0])
    return (pos * ret - turn * one_way_cost).fillna(0.0)


def metrics(r: pd.Series, pos: pd.Series = None) -> dict:
    if r.empty:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / 244
    dd = (eq / eq.cummax() - 1).min()
    vol = r.std(ddof=1) * np.sqrt(244)
    out = {"总收益": eq.iloc[-1] - 1, "年化": eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan,
           "最大回撤": dd, "夏普": (r.mean() * 244) / vol if vol > 0 else np.nan}
    if pos is not None:
        p = pos.reindex(r.index).fillna(0)
        out["在场时间"] = p.mean()
        out["进出次数"] = int((p.diff().abs() > 0).sum())
    return out


def fmt(rows: list) -> str:
    t = pd.DataFrame(rows)
    for c in ("总收益", "年化", "最大回撤", "在场时间"):
        if c in t:
            t[c] = t[c].map(lambda x: "" if pd.isna(x) else f"{x:+.1%}" if c != "在场时间" else f"{x:.0%}")
    if "进出次数" in t:
        t["进出次数"] = t["进出次数"].map(lambda x: "" if pd.isna(x) else str(int(x)))
    if "夏普" in t:
        t["夏普"] = t["夏普"].map(lambda x: f"{x:.2f}")
    return t.to_string(index=False)


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="20220801")
    ap.add_argument("--end", default="20260917")
    ap.add_argument("--etf-cost", type=float, default=0.0015, help="指数 ETF 往返成本")
    ap.add_argument("--basket-cost", type=float, default=0.0045, help="等权篮子往返成本")
    args = ap.parse_args()

    cfg = load_config()
    lo, hi = pd.Timestamp(args.start), pd.Timestamp(args.end)
    reg = regime_by_close(cfg)
    con = sqlite3.connect(cfg["data"]["db_path"])

    series = {}
    for code, name in INDICES.items():
        df = pd.read_sql("SELECT trade_date, open FROM daily_bar WHERE code = ? ORDER BY trade_date",
                         con, params=(code,))
        s = pd.Series(df["open"].to_numpy(float), index=pd.to_datetime(df["trade_date"].astype(str)))
        series[name] = (open_to_open(s), args.etf_cost / 2)

    # 全市场等权：每日再平衡，开盘到次日开盘
    _, codes, _ = collect_universe_pools(cfg, args.start, args.end)
    raw = load_daily_many(codes, start_date=args.start, end_date=args.end)
    rets = pd.concat({c: open_to_open(d["open"]) for c, d in raw.items() if d is not None and len(d)},
                     axis=1)
    rets = rets.clip(-0.5, 1.0)              # 防个别复权断点
    series["全市场等权"] = (rets.mean(axis=1), args.basket_cost / 2)

    # 持仓：第 t 天（t 日开盘到 t+1 日开盘）由 t−1 日收盘后的区间决定
    bull = (reg == "BULL").astype(float)
    print(f"区间 {args.start} ~ {args.end}；成本：指数往返 {args.etf_cost:.2%}，篮子往返 {args.basket_cost:.2%}")
    for part, (a, b) in (("全段", (lo, hi)), ("样本内", (lo, OOS - pd.Timedelta(days=1))),
                         ("样本外", (OOS, hi))):
        rows = []
        for name, (ret, cost) in series.items():
            ret = ret[(ret.index >= a) & (ret.index <= b)].dropna()
            pos = bull.shift(1).reindex(ret.index).fillna(0.0)
            hold = metrics(run(ret, pd.Series(1.0, index=ret.index), cost))
            timed = metrics(run(ret, pos, cost), pos)
            rows.append({"标的": name, "方式": "一直持有", **hold})
            rows.append({"标的": name, "方式": "择时持有", **timed})
        print(f"\n=== {part} {a.date()} ~ {b.date()} ===")
        print(fmt(rows))


if __name__ == "__main__":
    main()
