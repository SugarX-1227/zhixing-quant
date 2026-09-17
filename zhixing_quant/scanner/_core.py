"""选股扫描器公共内核。

三个扫描器（砖型图 / B1 / B2）原先是三份 95% 相同的代码，这里抽成一份。
每个策略只需要提供一个 StrategySpec，描述"用哪个指标函数、看哪个信号列、
候选行里多带哪些字段、理由怎么写"。

相比原实现修掉的问题：
1. 原来逐只调 load_daily，全市场 5000 只要 ~19 秒的数据库往返；
   改成 load_daily_many 批量取，同样规模约 1 秒。
2. 原来给**每只扫描过的**股票都存了 120 根 K 线到 chart_data，
   全市场扫描会把几十万行 DataFrame 塞进内存/session state。
   现在只给命中的候选存图表数据。
3. 原来 `if len(candidates) >= max_candidates: break` 会在凑够数量时
   提前结束扫描，后面的股票根本没看过，之后再按成交额排序其实没有意义。
   现在扫完整个股票池再排序截断（批量加载之后这么做也不慢）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from zhixing_quant.data.tdx_loader import (
    default_date_range,
    fetch_a_spot,
    filter_universe,
    load_daily_many,
)


@dataclass(frozen=True)
class StrategySpec:
    """一个选股策略需要提供的全部信息。"""

    key: str                                   # "brick" / "b1" / "b2"
    label: str                                 # 中文名，用于日志和界面
    signal_col: str                            # 信号列名，True 即命中
    add_indicators: Callable[[pd.DataFrame, dict], pd.DataFrame]
    min_bars: Callable[[dict], int]            # 该策略需要的最少 K 线根数
    extra_fields: Callable[[pd.Series], dict]  # 候选行额外字段
    reason: Callable[[pd.Series, dict], str]   # 命中理由文案


def scan(
    spec: StrategySpec,
    cfg: dict,
    end_date: Optional[str] = None,
    limit_universe: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """按 spec 扫描全市场。

    Args:
        spec: 策略描述。
        cfg: 配置字典。
        end_date: YYYYMMDD 信号日，默认取库里最新交易日。
        limit_universe: 只扫成交额前 N 只，None 表示整个股票池。
        progress: 可选回调 (已处理, 总数)，给 Web 界面画进度条用。

    Returns:
        (候选 DataFrame, {code: 带指标的 K 线 DataFrame})
        chart_data 只包含命中的候选。
    """
    spot = fetch_a_spot(trade_date=end_date)
    signal_date = int(spot["date"].iloc[0].strftime("%Y%m%d"))
    start_date, _ = default_date_range(str(signal_date))

    universe = filter_universe(spot, cfg)
    if limit_universe is not None and int(limit_universe) > 0:
        universe = universe.head(int(limit_universe))
    if universe.empty:
        return _empty_candidates(), {}

    codes = universe["code"].tolist()
    meta = universe.set_index("code")[["name", "amount"]].to_dict("index")
    min_bars = int(spec.min_bars(cfg))
    lookback = int(cfg.get("report", {}).get("lookback_bars", 120))
    total = len(codes)

    candidates: List[dict] = []
    chart_data: Dict[str, pd.DataFrame] = {}
    errors: Dict[str, str] = {}      # code -> 错误摘要，不再静默吞掉
    short_history = 0
    done = 0

    # 分批取，避免一次把整个市场的 DataFrame 都留在内存里
    for chunk in _chunks(codes, 500):
        frames = load_daily_many(
            chunk, start_date=start_date, end_date=str(signal_date), adjust="qfq"
        )
        for code in chunk:
            done += 1
            df = frames.get(code)
            if df is None or len(df) < min_bars:
                short_history += 1
                continue
            try:
                enriched = spec.add_indicators(df, cfg)
            except Exception as exc:
                # 单只股票算指标失败不该中断整轮扫描，但也不能当没发生：
                # 原实现直接 continue，于是「今天没有命中」和「整个池子
                # 全都算崩了」在界面上长得一模一样。现在记下来往外抛。
                errors[code] = f"{type(exc).__name__}: {exc}"
                continue
            if spec.signal_col not in enriched.columns:
                errors[code] = f"指标未产出信号列 {spec.signal_col}"
                continue
            latest = enriched.iloc[-1]
            if not bool(latest[spec.signal_col]):
                continue

            info = meta.get(code, {})
            row = {
                "date": enriched.index[-1].strftime("%Y-%m-%d"),
                "code": code,
                "name": info.get("name", ""),
                "close": float(latest["close"]),
                "amount": float(info.get("amount", 0.0)),
                "regime": cfg.get("regime", {}).get("current", ""),
                "adjust": df.attrs.get("adjust", "none"),
            }
            row.update(spec.extra_fields(latest))
            row["reason"] = spec.reason(latest, cfg)
            candidates.append(row)
            chart_data[code] = enriched.tail(lookback)
        if progress is not None:
            progress(done, total)

    diag = scan_warnings(total, short_history, errors)

    if not candidates:
        empty = _empty_candidates()
        empty.attrs["total_matches"] = 0
        empty.attrs["scanned"] = total
        empty.attrs["warnings"] = diag
        return empty, {}

    max_candidates = int(cfg.get("universe", {}).get("max_candidates", 10))
    df = pd.DataFrame(candidates).sort_values("amount", ascending=False)
    total_matches = len(df)
    df = df.head(max_candidates).reset_index(drop=True)
    # Keep the uncapped count available to the CLI/UI and self-check output.
    df.attrs["total_matches"] = total_matches
    df.attrs["scanned"] = total
    df.attrs["warnings"] = diag
    kept = set(df["code"])
    chart_data = {c: v for c, v in chart_data.items() if c in kept}
    return df, chart_data


def scan_warnings(total: int, short_history: int, errors: Dict[str, str]) -> List[str]:
    """把扫描过程中被跳过的标的归并成人能读的告警。

    抽成纯函数是为了能单测。判据：跳过的比例高到足以让"没命中"变成
    "没扫成"时必须说出来。回测执行器已经这么做了，扫描器原来没有。

    Args:
        total: 池内标的总数。
        short_history: 因 K 线不足被跳过的数量。
        errors: code -> 错误摘要。

    Returns:
        告警文案列表，一切正常时为空。
    """
    out: List[str] = []
    if errors:
        kinds: Dict[str, int] = {}
        for msg in errors.values():
            kinds[msg] = kinds.get(msg, 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[:3]
        detail = "；".join(f"{msg}（{n} 只）" for msg, n in top)
        out.append(f"{len(errors)} 只标的指标计算失败，已跳过：{detail}")
    if total and short_history / total >= 0.5:
        out.append(
            f"池内 {total} 只里有 {short_history} 只 K 线不足，没参与计算。"
            "多半是行情库还没补齐历史，此时「没有命中」不代表真的没有信号。"
        )
    if total and (short_history + len(errors)) >= total:
        out.append("没有任何一只标的被实际计算过，本次扫描结果无意义。")
    return out


def print_candidates(spec: StrategySpec, candidates: pd.DataFrame) -> None:
    """命令行输出。取代原来的 HTML 报告，结果直接看终端或 Web 界面。"""
    for w in candidates.attrs.get("warnings", []):
        print(f"⚠ {w}")
    if candidates.empty:
        print(f"{spec.label}：当日无符合条件的标的。")
        return
    total = int(candidates.attrs.get("total_matches", len(candidates)))
    shown = len(candidates)
    count = f"{shown}/{total}" if total > shown else str(shown)
    print(f"\n{spec.label}  {candidates['date'].iloc[0]}  命中 {count} 只\n")
    view = candidates.copy()
    view["成交额(亿)"] = (view["amount"] / 1e8).round(2)
    cols = ["code", "name", "close", "成交额(亿)"]
    cols += [c for c in ("kdj_j", "red_streak", "stop_loss") if c in view.columns]
    print(view[cols].to_string(index=False))
    print()
    for _, r in candidates.iterrows():
        print(f"  {r['code']} {r['name']}: {r['reason']}")
    if (candidates["adjust"] == "none").any():
        print(
            "\n注意：部分标的使用的是不复权价格，除权日附近可能出现假信号。"
            "\n      跑一次 `python -m zhixing_quant.data.sync --xdxr` 补上除权数据。"
        )


def build_cli(spec: StrategySpec):
    """生成该策略的命令行入口。"""
    import argparse
    import sys

    def main() -> None:
        parser = argparse.ArgumentParser(description=f"{spec.label}每日选股")
        parser.add_argument("--date", default=None, help="YYYYMMDD 信号日，默认库内最新交易日")
        parser.add_argument("--limit-universe", type=int, default=None, help="只扫前 N 只")
        args = parser.parse_args()

        from zhixing_quant.config import load_config
        from zhixing_quant.data.tdx_loader import DataNotReady

        cfg = load_config()
        try:
            candidates, _ = scan(
                spec, cfg, end_date=args.date, limit_universe=args.limit_universe
            )
        except DataNotReady as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        except Exception as exc:
            print(
                "扫描失败。请先确认本地行情库已同步：\n"
                "    python -m zhixing_quant.data.sync --names --xdxr\n"
                f"原始错误：{exc}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print_candidates(spec, candidates)

    return main


def _chunks(seq: List[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["date", "code", "name", "close", "amount", "regime", "adjust", "reason"]
    )
