"""盲评分析：看图标的「完美 / 一般 / 不要」，之后到底有没有跑赢？

三步，口径事先定死：

1. **标签 → 结果**：同一种信号内部，完美组 vs 不要组的超额收益。
   超额 = T+1 开盘买入到 T+h 收盘的收益 − 同日全市场（买得进的）等权平均，
   与 signal_event_study.py 同口径。t 检验 + Mann-Whitney 秩检验 + 置换检验。
2. **标签 → 特征**：完美组和不要组在信号日的量价特征上差在哪——
   这回答「看图的人实际在看什么」。
3. **特征 → 结果（大样本验证）**：250 张太少，第 2 步看到的差别只能当线索。
   对每个特征，在**全部**多头区间命中（B2 约 2 万、B1 约 8 万）上按五分位
   看 5 日超额，并分样本内外——标注用来发现，结论由大样本验证。

用法
----

    # 先用 ArtifactData 把 labels 集合导出成一个目录的 JSON（out_dir）
    python scripts/blind_label_analyze.py --labels-dir <目录>/labels

必须用全量数据。一次约 5 分钟（要过一遍全市场算同日基准）。
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from blind_label_sample import decode  # noqa: E402
from zhixing_quant.backtest.runner import collect_universe_pools  # noqa: E402
from zhixing_quant.config import load_config  # noqa: E402
from zhixing_quant.data.tdx_loader import load_daily_many  # noqa: E402
from zhixing_quant.indicators.pipeline import run_steps  # noqa: E402
from zhixing_quant.timing.active_value import regime_by_close  # noqa: E402

H = (5, 10, 20)
OOS = pd.Timestamp("2025-04-01")
FEATURES = {
    "当日涨幅%": "信号日涨跌幅",
    "量比昨日": "当日量 / 前一日量",
    "量比5日": "当日量 / 前 5 日均量",
    "上影占全幅": "上影线 / (最高-最低)",
    "下影占全幅": "下影线 / (最高-最低)",
    "收盘距黄线%": "收盘 / 黄线 - 1",
    "收盘距白线%": "收盘 / 白线 - 1",
    "白在黄上": "白线 > 黄线（1/0）",
    "白线5日斜率%": "白线 / 5 日前白线 - 1",
    "黄线10日斜率%": "黄线 / 10 日前黄线 - 1",
    "J值": "信号日 J",
    "前20日涨幅%": "收盘 / 20 日前收盘 - 1",
    "前60日涨幅%": "收盘 / 60 日前收盘 - 1",
    "距60日高点%": "收盘 / 前 60 日最高 - 1",
    "回调缩量比": "前 5 日均量 / 再前 20 日均量",
    "前20日涨停数": "前 20 日涨幅 ≥9.5%（20cm 票按 19.5%）的天数",
    "ATR占价%": "14 日 ATR / 收盘",
}


def log(*a) -> None:
    print(*a, flush=True)


def features(d: pd.DataFrame, code: str) -> pd.DataFrame:
    o, h, l, c, v = (d[k].astype(float) for k in ("open", "high", "low", "close", "vol"))
    rng = (h - l).replace(0, np.nan)
    wide = str(code).startswith(("300", "301", "688", "689"))
    lim = 19.5 if wide else 9.5
    pct = (c / c.shift(1) - 1) * 100
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return pd.DataFrame({
        "当日涨幅%": pct,
        "量比昨日": v / v.shift(1),
        "量比5日": v / v.shift(1).rolling(5).mean(),
        "上影占全幅": (h - pd.concat([o, c], axis=1).max(axis=1)) / rng,
        "下影占全幅": (pd.concat([o, c], axis=1).min(axis=1) - l) / rng,
        "收盘距黄线%": (c / d["yellow_line"] - 1) * 100,
        "收盘距白线%": (c / d["white_line"] - 1) * 100,
        "白在黄上": (d["white_line"] > d["yellow_line"]).astype(float),
        "白线5日斜率%": (d["white_line"] / d["white_line"].shift(5) - 1) * 100,
        "黄线10日斜率%": (d["yellow_line"] / d["yellow_line"].shift(10) - 1) * 100,
        "J值": d["kdj_j"],
        "前20日涨幅%": (c / c.shift(20) - 1) * 100,
        "前60日涨幅%": (c / c.shift(60) - 1) * 100,
        "距60日高点%": (c / h.shift(1).rolling(60).max() - 1) * 100,
        "回调缩量比": v.shift(1).rolling(5).mean() / v.shift(6).rolling(20).mean(),
        "前20日涨停数": (pct.shift(1) >= lim).astype(float).rolling(20).sum(),
        "ATR占价%": tr.rolling(14).mean() / c * 100,
    }, index=d.index)


def build(cfg: dict, start: str, end: str) -> pd.DataFrame:
    """全部多头区间 B1/B2 命中：特征 + 各持有期超额。同日基准用全市场累加，不存全表。"""
    reg = regime_by_close(cfg)
    _, codes, _ = collect_universe_pools(cfg, start, end)
    warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    raw = load_daily_many(codes, start_date=warm, end_date=end)
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    sums = {h: pd.Series(dtype=float) for h in H}
    cnts = {h: pd.Series(dtype=float) for h in H}
    hits = []
    for code, df in raw.items():
        if df is None or len(df) < 150:
            continue
        d = run_steps(df, cfg, ["b1", "b2"]).df
        o1 = d["open"].shift(-1)
        fill = (~((o1 == d["high"].shift(-1)) & (o1 == d["low"].shift(-1))
                  & (o1 / d["close"] - 1 > 0.09)) & o1.notna())
        inwin = (d.index >= lo) & (d.index <= hi)
        rets = {h: (d["close"].shift(-h) / o1 - 1.0) for h in H}
        for h in H:
            r = rets[h][fill & inwin].dropna()
            sums[h] = sums[h].add(r, fill_value=0)
            cnts[h] = cnts[h].add(pd.Series(1.0, index=r.index), fill_value=0)
        bull = pd.Series(reg.reindex(d.index).to_numpy() == "BULL", index=d.index)
        for kind, col in (("B2", "sig_b2"), ("B1", "sig_b1")):
            m = d[col].fillna(False).astype(bool) & bull & fill & inwin
            if not m.any():
                continue
            f = features(d, code)[m]
            f["kind"], f["code"] = kind, code
            for h in H:
                f[f"r{h}"] = rets[h][m]
            hits.append(f)
    ev = pd.concat(hits)
    ev.index.name = "date"
    ev = ev.reset_index()
    for h in H:
        base = sums[h] / cnts[h]
        ev[f"x{h}"] = ev[f"r{h}"] - ev["date"].map(base)
    return ev


def perm_p(a: np.ndarray, b: np.ndarray, n: int = 20000, seed: int = 0) -> float:
    rng = np.random.default_rng(seed)
    obs = a.mean() - b.mean()
    pool = np.concatenate([a, b])
    k = len(a)
    hit = 0
    for _ in range(n):
        rng.shuffle(pool)
        hit += abs(pool[:k].mean() - pool[k:].mean()) >= abs(obs)
    return (hit + 1) / (n + 1)


def mw_p(a: np.ndarray, b: np.ndarray) -> float:
    """Mann-Whitney U 的正态近似双侧 p（不引 scipy）。"""
    x = pd.Series(np.concatenate([a, b])).rank().to_numpy()
    n1, n2 = len(a), len(b)
    u = x[:n1].sum() - n1 * (n1 + 1) / 2
    mu, sd = n1 * n2 / 2, np.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    z = (u - mu) / sd
    from math import erf, sqrt
    return float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--labels-dir", required=True)
    ap.add_argument("--start", default="20220801")
    ap.add_argument("--end", default="20260917")
    args = ap.parse_args()

    rows = []
    for f in glob.glob(f"{args.labels_dir}/*.json"):
        body = json.load(open(f, encoding="utf-8"))
        code, date = decode(Path(f).stem)
        rows.append({"code": code, "date": pd.Timestamp(date), "kind": body["kind"],
                     "label": body["label"], "note": body.get("note", "")})
    lab = pd.DataFrame(rows)
    log(f"标注 {len(lab)} 张")

    cfg = load_config()
    ev = build(cfg, args.start, args.end)
    log(f"全部多头区间命中：B2 {int((ev.kind == 'B2').sum()):,}，B1 {int((ev.kind == 'B1').sum()):,}")
    m = lab.merge(ev, on=["date", "code", "kind"], how="left")
    miss = int(m["x5"].isna().sum())
    if miss:
        log(f"⚠ {miss} 张没对上结果（多为信号日太靠近数据末尾），不计入")
    m = m.dropna(subset=["x5"])

    pct = lambda x: f"{x:+.2%}"  # noqa: E731
    for kind in ("B2", "B1"):
        s = m[m.kind == kind]
        pop = ev[ev.kind == kind]
        log(f"\n################ {kind}（标注 {len(s)} 张；全部多头命中 5 日超额均值 {pct(pop.x5.mean())}）")
        log("=== 1. 标签 → 结果 ===")
        tab = []
        for l in ("完美", "一般", "不要"):
            g = s[s.label == l]
            row = {"标签": l, "张数": len(g)}
            for h in H:
                row[f"{h}日超额"] = pct(g[f"x{h}"].mean())
            row["5日中位"] = pct(g.x5.median())
            row["5日跑赢比例"] = f"{(g.x5 > 0).mean():.0%}"
            tab.append(row)
        log(pd.DataFrame(tab).to_string(index=False))
        a, b = s[s.label == "完美"], s[s.label == "不要"]
        for h in H:
            xa, xb = a[f"x{h}"].to_numpy(), b[f"x{h}"].to_numpy()
            diff = xa.mean() - xb.mean()
            se = np.sqrt(xa.var(ddof=1) / len(xa) + xb.var(ddof=1) / len(xb))
            log(f"  完美 − 不要，{h:>2} 日：{pct(diff)}  t={diff / se:+.2f}  "
                f"秩检验 p={mw_p(xa, xb):.3f}  置换 p={perm_p(xa, xb):.3f}")
        score = s.label.map({"完美": 2, "一般": 1, "不要": 0})
        rho = pd.Series(score.to_numpy()).rank().corr(pd.Series(s.x5.to_numpy()).rank())
        log(f"  标签等级与 5 日超额的秩相关 ρ = {rho:+.3f}")

        log("=== 2. 标签 → 特征（完美 vs 不要；标准化差 = 均值差 / 合并标准差）===")
        ft = []
        for f, desc in FEATURES.items():
            xa, xb = a[f].dropna().to_numpy(), b[f].dropna().to_numpy()
            sd = np.sqrt((xa.var(ddof=1) + xb.var(ddof=1)) / 2) or np.nan
            ft.append({"特征": f, "完美均值": round(xa.mean(), 2), "不要均值": round(xb.mean(), 2),
                       "标准化差": (xa.mean() - xb.mean()) / sd, "秩检验p": mw_p(xa, xb)})
        ft = pd.DataFrame(ft).sort_values("秩检验p")
        log(ft.round(3).to_string(index=False))

        log("=== 3. 特征 → 结果（全部多头命中，按特征五分位看 5 日超额，样本内 / 样本外）===")
        out = []
        for f in ft[ft["秩检验p"] < 0.10]["特征"]:
            row = {"特征": f, "看图偏好": "高" if ft.set_index("特征").loc[f, "标准化差"] > 0 else "低"}
            for part, sub in (("内", pop[pop.date < OOS]), ("外", pop[pop.date >= OOS])):
                q = pd.qcut(sub[f].rank(method="first"), 5, labels=False)
                means = sub.groupby(q)["x5"].mean()
                row[f"{part}_Q1(低)"] = pct(means.iloc[0])
                row[f"{part}_Q5(高)"] = pct(means.iloc[-1])
                row[f"{part}_Q5−Q1"] = pct(means.iloc[-1] - means.iloc[0])
            out.append(row)
        if out:
            log(pd.DataFrame(out).to_string(index=False))
        else:
            log("  没有 p < 0.10 的特征")


if __name__ == "__main__":
    main()
