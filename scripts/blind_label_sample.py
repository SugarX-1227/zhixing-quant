"""盲评抽样：从历史 B1/B2 命中里随机抽图，截到信号当天，供使用者看图打标签。

为什么要盲评
------------

课程案例和记忆里「一眼就对」的图形几乎都是事后挑出来的赢家。当时看着一样、
后来失败的不会出现在课件里。实测：通用 B2 在 16 个可查的课程完美图形里
12 个当天触发，但全部 B2 命中的平均 5 日超额是 -0.51%。要知道看图判断有没有
真实优势，只能在**看不到后续**的条件下给一批历史命中打标签，再看被标为
「完美」的是否跑赢。

规则（事先定死）
----------------

- 人群：多头区间下、T+1 买得进（非一字涨停）的 sig_b1 / sig_b2 命中，
  信号日在 --start ~ --end 之间（end 之后至少留 20 个交易日看后续）。
- 使用者提供的完美图形（B1 十张 + B2 二十二张）涉及的股票整只排除，免得认出来。
- 每只股票最多出现一次；随机种子固定，重跑得到同一批图、同一顺序。
- 图里只有信号日及之前的 120 根 K 线；页面不出现代码和日期，
  只有一个可还原的编号（--decode 还原）。

用法
----

    python scripts/blind_label_sample.py --out /path/items.json
    python scripts/blind_label_sample.py --decode <编号>
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
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

BARS = 120
SEED = 20260923
KEY = b"zhixing-blind"

# 使用者提供的完美图形涉及的股票（按名称匹配，「昂立康」疑为「昂利康」笔误，两个都排除）
PERFECT_NAMES = [
    "华纳药厂", "微芯生物", "宁波韵升", "光电股份", "昂立康", "昂利康", "澄天伟业",
    "方正科技", "野马电池", "新瀚新材", "国轩高科", "三峡水利", "太钢不锈", "爱博医疗",
    "安德利", "潮宏基", "路桥信息", "晨光生物", "京北方", "美迪西", "指南针", "国义招标",
    "海天瑞声", "德龙激光", "中坚科技", "晶科科技", "中富电路", "国盛", "昆仑万维",
    "财富趋势", "世运电路", "中煤能源", "百普赛斯", "南亚新材", "四会富仕", "英飞特",
]


def encode(code: str, date: str) -> str:
    raw = f"{code}|{date}".encode()
    x = bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(raw))
    return base64.b32encode(x).decode().rstrip("=").lower()


def decode(item_id: str) -> tuple:
    s = item_id.upper()
    x = base64.b32decode(s + "=" * (-len(s) % 8))
    code, date = bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(x)).decode().split("|")
    return code, date


def excluded_codes(cfg: dict) -> set:
    import sqlite3
    con = sqlite3.connect(cfg["data"]["db_path"])
    sec = pd.read_sql("SELECT code, name FROM security", con)
    names = sec["name"].str.replace(" ", "")
    mask = np.zeros(len(sec), bool)
    for n in PERFECT_NAMES:
        mask |= names.str.contains(n).to_numpy()
    return set(sec.loc[mask, "code"])


def chart_payload(d: pd.DataFrame, pos: int) -> dict:
    w = d.iloc[pos - BARS + 1: pos + 1]
    c = d["close"]
    bbi = (c.rolling(3).mean() + c.rolling(6).mean()
           + c.rolling(12).mean() + c.rolling(24).mean()) / 4
    r2 = lambda s: [None if pd.isna(v) else round(float(v), 2) for v in s]  # noqa: E731
    r1 = lambda s: [None if pd.isna(v) else round(float(v), 1) for v in s]  # noqa: E731
    vol = d["vol"]
    return {
        "o": r2(w["open"]), "h": r2(w["high"]), "l": r2(w["low"]), "c": r2(w["close"]),
        "v": [int(x) for x in w["vol"]],
        "vma5": [int(x) for x in vol.rolling(5).mean().iloc[pos - BARS + 1: pos + 1].fillna(0)],
        "vma10": [int(x) for x in vol.rolling(10).mean().iloc[pos - BARS + 1: pos + 1].fillna(0)],
        "white": r2(w["white_line"]), "yellow": r2(w["yellow_line"]),
        "bbi": r2(bbi.iloc[pos - BARS + 1: pos + 1]),
        "k": r1(w["kdj_k"]), "d": r1(w["kdj_d"]), "j": r1(w["kdj_j"]),
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out")
    ap.add_argument("--decode")
    ap.add_argument("--start", default="20220901")
    ap.add_argument("--end", default="20260814")
    ap.add_argument("--n-b2", type=int, default=150)
    ap.add_argument("--n-b1", type=int, default=100)
    args = ap.parse_args()
    if args.decode:
        print(*decode(args.decode))
        return
    if not args.out:
        sys.exit("需要 --out 或 --decode")

    cfg = load_config()
    skip = excluded_codes(cfg)
    reg = regime_by_close(cfg)
    _, codes, _ = collect_universe_pools(cfg, args.start, args.end)
    codes = [c for c in codes if c not in skip]
    warm = (pd.Timestamp(args.start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
    raw = load_daily_many(codes, start_date=warm, end_date="20260917")
    lo, hi = pd.Timestamp(args.start), pd.Timestamp(args.end)

    frames, cands = {}, []
    for code, df in raw.items():
        if df is None or len(df) < BARS + 30:
            continue
        d = run_steps(df, cfg, ["b1", "b2"]).df
        o1 = d["open"].shift(-1)
        fill = ~((o1 == d["high"].shift(-1)) & (o1 == d["low"].shift(-1))
                 & (o1 / d["close"] - 1 > 0.09)) & o1.notna()
        bull = reg.reindex(d.index).to_numpy() == "BULL"
        idx = np.arange(len(d))
        ok = fill.to_numpy() & bull & (idx >= BARS + 10) & (d.index >= lo) & (d.index <= hi)
        for kind, col in (("B2", "sig_b2"), ("B1", "sig_b1")):
            for p in np.flatnonzero(ok & d[col].fillna(False).to_numpy(bool)):
                cands.append((kind, code, int(p)))
        frames[code] = d
    print(f"候选：B2 {sum(k == 'B2' for k, *_ in cands):,}，B1 {sum(k == 'B1' for k, *_ in cands):,}，"
          f"排除完美图形股票 {len(skip)} 只")

    rng = np.random.default_rng(SEED)
    order = rng.permutation(len(cands))
    picked, used, want = [], set(), {"B2": args.n_b2, "B1": args.n_b1}
    for i in order:
        kind, code, p = cands[i]
        if want[kind] == 0 or code in used:
            continue
        used.add(code)
        want[kind] -= 1
        picked.append((kind, code, p))
        if not any(want.values()):
            break
    rng.shuffle(picked)

    items = []
    for kind, code, p in picked:
        d = frames[code]
        date = d.index[p].strftime("%Y%m%d")
        items.append({"id": encode(code, date), "kind": kind, **chart_payload(d, p)})
    Path(args.out).write_text(json.dumps({"bars": BARS, "items": items},
                                         ensure_ascii=False, separators=(",", ":")))
    print(f"抽中 B2 {sum(i['kind'] == 'B2' for i in items)} 张、B1 {sum(i['kind'] == 'B1' for i in items)} 张 "
          f"→ {args.out}（{Path(args.out).stat().st_size / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
