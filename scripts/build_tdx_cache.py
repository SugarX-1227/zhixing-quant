"""把通达信 MCP 落盘的 K 线结果文件转换为标准 CSV 缓存。

用法:
    .venv/bin/python scripts/build_tdx_cache.py <tool_results_dir> <start_date> <end_date> [--adjust qfq]

说明:
    通达信 MCP (tdx_kline) 返回较大时结果会保存为
    mcp-connector-proxy-tdx-connector_tdx_kline-*.txt 文件（纯文本 + JSON）。
    本脚本扫描这些文件，按 Code 解析 Rows，写入:
        data/cache/daily_{code}_{start}_{end}_{adjust}.csv
    供 zhixing_quant.data.tdx_loader 读取。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data" / "cache"

KLINE_PATTERN = "mcp-connector-proxy-tdx-connector_tdx_kline-*.txt"


def extract_json(text: str) -> dict:
    """Extract the JSON payload (after 详细K线数据:) from a MCP result file."""
    marker = "详细K线数据:"
    idx = text.find(marker)
    start = idx + len(marker) if idx >= 0 else 0
    first_brace = text.find("{", start)
    last_brace = text.rfind("}")
    if first_brace < 0 or last_brace <= first_brace:
        raise ValueError("no JSON payload found")
    return json.loads(text[first_brace : last_brace + 1])


def parse_kline_file(path: Path) -> dict:
    """Parse one result file into {code, name, df}."""
    payload = extract_json(path.read_text(encoding="utf-8", errors="replace"))
    code = str(payload["Code"]).zfill(6)
    name = payload.get("AttachInfo", {}).get("Name", "")
    rows = payload.get("Rows", [])
    records = []
    for row in rows:
        records.append(
            {
                "trade_date": str(row["Data"]),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "vol": float(row["Volume"]) * 100.0,  # 手 -> 股
                "amount": float(row["Amount"]),
            }
        )
    df = pd.DataFrame(records)
    if not df.empty:
        df["pct_chg"] = (df["close"] / df["close"].shift(1) - 1) * 100.0
    return {"code": code, "name": name, "df": df}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool_results_dir", help="MCP 结果落盘目录")
    parser.add_argument("start_date", help="YYYYMMDD 起始日期（用于缓存命名）")
    parser.add_argument("end_date", help="YYYYMMDD 结束日期")
    parser.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""])
    args = parser.parse_args()

    results_dir = Path(args.tool_results_dir)
    files = sorted(results_dir.glob(KLINE_PATTERN))
    if not files:
        print(f"[ERROR] 未找到 K 线结果文件: {results_dir / KLINE_PATTERN}", file=sys.stderr)
        return 1

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    parsed: dict[str, dict] = {}
    for path in files:
        try:
            item = parse_kline_file(path)
        except Exception as exc:
            print(f"[WARN] 解析失败 {path.name}: {exc}")
            continue
        # 同名代码保留最新文件（文件按 mtime 排序，后写的更新）
        if item["code"] not in parsed or path.stat().st_mtime > 0:
            parsed[item["code"]] = item

    written, empty = 0, 0
    for code in sorted(parsed):
        item = parsed[code]
        df = item["df"]
        if df.empty:
            empty += 1
            continue
        out_path = CACHE_DIR / f"daily_{code}_{args.start_date}_{args.end_date}_{args.adjust}.csv"
        df.to_csv(out_path, index=False)
        written += 1
        print(f"[OK] {code} {item['name']}: {len(df)} 根K线 -> {out_path.name}")

    print(f"\n共解析 {len(parsed)} 只，写入 {written} 只，空数据 {empty} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
