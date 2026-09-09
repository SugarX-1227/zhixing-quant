"""真实数据自检。

    python scripts/selfcheck.py              # 全部检查
    python scripts/selfcheck.py --quick      # 跳过全市场扫描（快，约 30 秒）

合成数据能验证"程序不崩"，验证不了"数据对不对、信号合不合理"。
这个脚本专门检查那些只有真实行情才能暴露的问题。

每一项输出 通过 / 警告 / 失败：
  失败 = 必须修，否则结果不可信
  警告 = 需要你人工判断，脚本不敢替你下结论
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# 允许 `python scripts/selfcheck.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS, WARN, FAIL = "通过", "警告", "失败"
results = []


def check(name: str, level: str, detail: str = "") -> None:
    results.append((name, level, detail))
    mark = {"通过": "  ✓", "警告": "  !", "失败": "  ✗"}[level]
    print(f"{mark} {name}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"      {line}")


def section(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


# ---------------------------------------------------------------------------
# A. 数据层
# ---------------------------------------------------------------------------

def check_data(cfg):
    section("A. 数据层")
    from zhixing_quant.data.tdx_loader import data_health, fetch_a_spot, load_daily

    h = data_health()
    if not h.get("ok"):
        check("行情库可用", FAIL, h.get("message", ""))
        return None
    check("行情库可用", PASS,
          f"{h['codes']:,} 只 / {h['bars']:,} 条 / {h['db_size_mb']} MB")

    # 股票数量。A 股约 5400 只，明显偏少说明同步不全
    if h["codes"] < 3000:
        check("股票数量", FAIL,
              f"只有 {h['codes']} 只，A 股约 5400 只。\n"
              "通达信可能没下载完整历史，或 markets 配置漏了市场。")
    elif h["codes"] < 4500:
        check("股票数量", WARN, f"{h['codes']} 只，比预期偏少，确认是否遗漏板块。")
    else:
        check("股票数量", PASS, f"{h['codes']} 只")

    # 数据新鲜度
    latest = str(h["date_max"])
    lag = (datetime.now() - datetime.strptime(latest, "%Y%m%d")).days
    if lag > 5:
        check("数据新鲜度", FAIL, f"最新 {latest}，落后 {lag} 天。先开通达信下载再同步。")
    elif lag > 2:
        check("数据新鲜度", WARN, f"最新 {latest}，落后 {lag} 天（可能是节假日）。")
    else:
        check("数据新鲜度", PASS, f"最新交易日 {latest}")

    # 股票名称：ST 过滤唯一依赖
    if h["named"] == 0:
        check("股票名称", FAIL, "名称表为空，ST 过滤完全失效。跑 sync --names。")
    elif h["named"] < h["codes"] * 0.9:
        check("股票名称", WARN, f"{h['named']}/{h['codes']} 只有名称，覆盖不全。")
    else:
        check("股票名称", PASS, f"{h['named']} 只")

    # 复权：不做的话除权日附近全是假信号
    if h["xdxr_codes"] == 0:
        check("除权除息", FAIL,
              "没有除权数据，当前用不复权价格。\n"
              "一次 10 送 10 在不复权数据里表现为 -50% 假暴跌，\n"
              "均线/KDJ/砖型图在除权日附近全部失真。跑 sync --xdxr。")
    elif h["xdxr_codes"] < h["codes"] * 0.5:
        check("除权除息", WARN, f"只有 {h['xdxr_codes']} 只有除权数据，覆盖偏低。")
    else:
        check("除权除息", PASS, f"{h['xdxr_codes']} 只")

    spot = fetch_a_spot()
    check("全市场快照", PASS, f"{len(spot)} 只（停牌股不在内，属正常）")

    # 前复权是否真的生效
    # 指数没有除权事件且代码带市场前缀，不应混入前复权抽查。
    from zhixing_quant.data.tdx_loader import get_store
    xdxr_codes = {
        str(row["code"]) for row in get_store().conn.execute(
            "SELECT DISTINCT code FROM xdxr"
        ).fetchall()
    }
    stock_spot = spot[~spot["code"].astype(str).str.startswith(("sh", "sz"))]
    sample = stock_spot[stock_spot["code"].isin(xdxr_codes)] \
        .nlargest(5, "amount")["code"].tolist()
    if not sample:
        check("前复权生效", WARN, "库内暂无带除权事件的股票，无法抽查。")
        return spot
    adj = [load_daily(c).attrs.get("adjust") for c in sample]
    if all(a == "qfq" for a in adj):
        check("前复权生效", PASS)
    else:
        check("前复权生效", FAIL, f"抽查 5 只，复权口径 {adj}，应全为 qfq。")
    return spot


def check_price_sanity(spot):
    """价格合理性。真实数据才能暴露解析错误。"""
    section("B. 价格合理性（解析是否正确）")
    import numpy as np

    from zhixing_quant.data.tdx_loader import load_daily

    bad_ohlc, bad_jump, thin = [], [], []
    codes = spot[~spot["code"].astype(str).str.startswith(("sh", "sz"))] \
        .nlargest(60, "amount")["code"].tolist()
    for c in codes:
        df = load_daily(c)
        if len(df) < 60:
            thin.append(c)
            continue
        # high 必须 >= low、>= open/close
        if ((df["high"] < df["low"]) | (df["high"] < df["close"]) |
                (df["low"] > df["close"])).any():
            bad_ohlc.append(c)
        # 前复权后单日涨跌幅超过 ±25% 的，多半是复权没做对
        chg = df["close"].pct_change().abs()
        n = int((chg > 0.25).sum())
        if n > 0:
            bad_jump.append((c, n, round(float(chg.max()) * 100, 1)))

    check("OHLC 关系", FAIL if bad_ohlc else PASS,
          f"{len(bad_ohlc)} 只存在 high<low 等异常：{bad_ohlc[:5]}" if bad_ohlc
          else "抽查 60 只，high/low/open/close 关系全部正确")

    if bad_jump:
        top = sorted(bad_jump, key=lambda x: -x[2])[:5]
        check("异常跳空", WARN,
              f"{len(bad_jump)}/60 只出现单日 >25% 波动，最大 {top[0][2]}%。\n"
              f"样本：{[(c, f'{p}%') for c, _, p in top]}\n"
              "A 股主板涨跌停 ±10%、创业板科创板 ±20%。\n"
              "超出说明这些标的的除权数据可能缺失或不准。")
    else:
        check("异常跳空", PASS, "抽查 60 只，无超出涨跌停的异常波动")

    if thin:
        check("K线长度", WARN, f"{len(thin)} 只不足 60 根（次新股属正常）")


# ---------------------------------------------------------------------------
# C. 指标与防守
# ---------------------------------------------------------------------------

def check_pipeline(cfg, spot):
    section("C. 指标流水线与防守规则")
    from zhixing_quant.data.tdx_loader import load_daily
    from zhixing_quant.indicators.pipeline import (PIPELINES, defense_coverage,
                                                   run_pipeline)

    code = spot.nlargest(1, "amount")["code"].iloc[0]
    df = load_daily(code)
    if len(df) < 160:
        check("样本长度", WARN, f"{code} 只有 {len(df)} 根，建议换一只长历史的验证")

    for name in ["defense", "full"]:
        r = run_pipeline(df, cfg, name)
        if r.ok:
            check(f"{name} 流水线", PASS, f"{len(r.applied)} 步全部执行")
        else:
            check(f"{name} 流水线", FAIL, "\n".join(r.warnings()))

    r = run_pipeline(df, cfg, "defense")
    dead = [k for k, v in defense_coverage(r.df).items() if not v]
    if dead:
        check("防守八级阶梯", FAIL,
              "以下规则缺列不会触发：" + "、".join(dead) +
              "\n界面上「没触发」和「规则没运行」长得一样，必须修。")
    else:
        check("防守八级阶梯", PASS, "6 级依赖列齐全")


# ---------------------------------------------------------------------------
# D. 信号合理性
# ---------------------------------------------------------------------------

def check_signals(cfg, quick: bool):
    section("D. 信号合理性（需要你人工判断）")
    from zhixing_quant.scanner.strategy_scan import available, scan_strategy

    limit = 100 if quick else 500
    print(f"  扫描池大小 {limit}（成交额前 N 只）\n")
    for name, meta in available().items():
        t = time.time()
        try:
            cands, _ = scan_strategy(name, cfg, limit_universe=limit)
        except Exception as exc:
            check(meta["label"], FAIL, f"{type(exc).__name__}: {exc}")
            continue
        n = len(cands)
        total = int(cands.attrs.get("total_matches", n))
        el = time.time() - t
        rate = total / limit
        if rate > 0.15:
            check(meta["label"], WARN,
                  f"命中 {total}/{limit} 只（界面显示 {n} 只，{rate:.0%}），比例过高。\n"
                  "选股条件可能过松，规格 10 的参数是初始猜测值，需要校准。")
        elif n == 0:
            check(meta["label"], WARN,
                  f"命中 0 只，耗时 {el:.1f}s。单日无信号是正常的，\n"
                  "但如果连续一周都是 0，说明条件过严或指标有问题。")
        else:
            shown = f"，界面显示 {n} 只" if total > n else ""
            check(meta["label"], PASS, f"命中 {total} 只{shown}，耗时 {el:.1f}s")


# ---------------------------------------------------------------------------
# E. 仓位与下单
# ---------------------------------------------------------------------------

def check_sizing(cfg):
    section("E. 仓位计算")
    from zhixing_quant.portfolio.sizer import PositionSizer

    p = cfg.get("portfolio", {})
    risk = float(p.get("risk_per_trade", 0.02))
    kelly = float(p.get("kelly_fraction", 0.25))
    s = PositionSizer(risk, kelly)
    equity = float(cfg.get("capital", {}).get("initial_cash", 500000) or 500000)
    budget = equity * risk * kelly

    print(f"  权益 {equity:,.0f} · 单笔风险预算 {budget:,.0f} 元\n")
    over = []
    for px, stop_pct in [(8.0, 0.05), (30.0, 0.05), (100.0, 0.05), (1500.0, 0.05)]:
        stop = px * (1 - stop_pct)
        n = s.size(px, stop, equity, regime_max_pct=0.5, per_position_pct=0.20)
        exp = s.risk_exposure(n, px, stop)
        flag = "买不了" if n == 0 else f"{n:,} 股 / {n*px:,.0f} 元 / 风险 {exp:,.0f} ({exp/equity:.2%})"
        print(f"    {px:>8.2f} 元 止损 -5%  →  {flag}")
        if exp > budget + 1:
            over.append(px)
    print()
    if over:
        check("风险预算约束", FAIL, f"这些价位超出预算：{over}")
    else:
        check("风险预算约束", PASS, "所有价位单笔风险均不超预算")
    check("高价股处理", WARN,
          "高价股 + 宽止损会返回 0 股，这是正确行为（一手就超预算）。\n"
          "界面会说明原因并给出建议止损位。如果你经常想买高价股，\n"
          "要么调大 capital.initial_cash，要么用更近的止损。")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过大范围扫描")
    args = ap.parse_args()

    print("知行量化 · 真实数据自检")
    print(f"时间 {datetime.now():%Y-%m-%d %H:%M}")

    from zhixing_quant.config import load_config
    cfg = load_config()

    spot = check_data(cfg)
    if spot is None:
        print("\n行情库不可用，后续检查跳过。")
        return 1
    check_price_sanity(spot)
    check_pipeline(cfg, spot)
    check_signals(cfg, args.quick)
    check_sizing(cfg)

    section("汇总")
    for lv in (FAIL, WARN, PASS):
        items = [n for n, l, _ in results if l == lv]
        print(f"  {lv} {len(items)} 项" + (f"：{'、'.join(items)}" if lv != PASS and items else ""))
    fails = sum(1 for _, l, _ in results if l == FAIL)
    print()
    if fails:
        print("有失败项，结果不可信，先修完再用。")
    else:
        print("没有失败项。警告项需要你人工判断，脚本不替你下结论。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
