"""Daily B2 stock scanner."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from zhixing_quant.config import load_config
from zhixing_quant.data.tdx_loader import fetch_a_spot, filter_universe, load_daily
from zhixing_quant.indicators.b2 import add_b2_indicators
from zhixing_quant.reports.html import render_daily_report


def scan_daily(cfg: dict, end_date: str = None, limit_universe: int = None) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """Scan沪深 A股 for the B2 signal.

    Args:
        cfg: Config dictionary.
        end_date: YYYYMMDD end date. Defaults to today.
        limit_universe: Optional maximum number of stocks to scan for smoke tests.

    Returns:
        Candidate DataFrame and chart data mapping.
    """
    end_date = end_date or datetime.now().strftime("%Y%m%d")
    start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=260)).strftime("%Y%m%d")
    spot = fetch_a_spot(cache=True)
    universe = filter_universe(spot, cfg)
    if limit_universe is not None:
        universe = universe.head(limit_universe)

    candidates: List[dict] = []
    chart_data: Dict[str, pd.DataFrame] = {}
    max_candidates = int(cfg["universe"]["max_candidates"])
    for _, stock in universe.iterrows():
        code = str(stock["code"])
        try:
            daily = load_daily(code, start_date=start_date, end_date=end_date, adjust="qfq", cache=True)
            if len(daily) < int(cfg["b2"]["exist_window"]):
                continue
            enriched = add_b2_indicators(daily, cfg)
            latest = enriched.iloc[-1]
            chart_data[code] = enriched.tail(int(cfg["report"]["lookback_bars"]))
            if not bool(latest["sig_b2"]):
                continue
            row = {
                "date": enriched.index[-1].strftime("%Y-%m-%d"),
                "code": code,
                "name": stock["name"],
                "close": float(latest["close"]),
                "amount": float(stock["amount"]),
                "kdj_j": float(latest["kdj_j"]),
                "kdj_k": float(latest["kdj_k"]),
                "kdj_d": float(latest["kdj_d"]),
                "pct_chg": float(latest["pct_chg"]),
                "regime": cfg["regime"]["current"],
                "reason": (
                    f"近{cfg['b2']['exist_window']}日J<{cfg['b2']['j_oversold']}"
                    f" + 涨幅>{cfg['b2']['chg_min']}%"
                    f" + 放量"
                    f" + J值({float(latest['kdj_j']):.1f})<{cfg['b2']['j_threshold']}"
                ),
            }
            candidates.append(row)
            if len(candidates) >= max_candidates:
                break
        except Exception as exc:
            print(f"[WARN] skip {code} {stock['name']}: {exc}")

    candidates_df = pd.DataFrame(candidates)
    if not candidates_df.empty:
        candidates_df = candidates_df.sort_values(
            ["amount", "kdj_j"], ascending=[False, True]
        ).head(max_candidates)
    return candidates_df, chart_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run daily B2 stock scanner.")
    parser.add_argument("--date", default=None, help="YYYYMMDD end date, default today")
    parser.add_argument("--limit-universe", type=int, default=None, help="Only scan first N stocks")
    args = parser.parse_args()

    cfg = load_config()
    try:
        candidates, chart_data = scan_daily(cfg, end_date=args.date, limit_universe=args.limit_universe)
    except Exception as exc:
        print(
            "通达信数据缓存缺失或数据不足。请先通过通达信 MCP 同步当日行情"
            "(tdx_screener 选股 + tdx_kline 日K) 后重试。原始错误："
            f"{exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    report_date = args.date or datetime.now().strftime("%Y%m%d")
    path = render_daily_report(report_date, candidates, chart_data, cfg, strategy="b2")
    print(f"Report: {path}")
    print(f"Candidates: {len(candidates)}")


if __name__ == "__main__":
    main()
