"""增量同步：把通达信 vipdoc 下的 .day 文件读进本地 SQLite。

用法：
    python -m zhixing_quant.data.sync              # 增量同步（每天盘后跑）
    python -m zhixing_quant.data.sync --full       # 忽略偏移量，全量重建
    python -m zhixing_quant.data.sync --names      # 顺带更新股票名称（需要 pytdx）
    python -m zhixing_quant.data.sync --xdxr       # 顺带更新除权除息（需要 pytdx）

增量原理：
    .day 是 32 字节定长记录，sync_state 表记了上次读到第几条。
    只要 (a) 文件变大了 且 (b) 上次最后一条记录在文件里还对得上，
    就从 offset = record_count * 32 开始 seek，只读新增的几条。
    对不上说明通达信重写了文件（换了复权口径 / 修数据），退化成整只全量重读。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from zhixing_quant.config import load_config
from zhixing_quant.data import tdx_reader as tr
from zhixing_quant.data.store import BarStore

BATCH_SIZE = 400


def resolve_paths(cfg: dict) -> tr.TdxPaths:
    install_dir = cfg.get("data", {}).get("tdx_install_dir")
    if not install_dir:
        raise SystemExit(
            "请先在 config/settings.yaml 里配置:\n"
            "data:\n"
            "  tdx_install_dir: C:/new_tdx      # 或 /Users/you/tdx"
        )
    return tr.TdxPaths.from_install_dir(install_dir)


def db_path(cfg: dict) -> Path:
    from zhixing_quant.config import PROJECT_ROOT

    p = cfg.get("data", {}).get("db_path", "data/market.db")
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _plan_read(
    entry: dict, state: Optional[dict], full: bool
) -> Tuple[int, bool]:
    """决定这只股票该从哪个字节偏移开始读。

    Returns:
        (start_offset, is_full_reread)
    """
    if full or state is None:
        return 0, True

    prev_count = int(state["record_count"])
    prev_size = int(state["file_size"])
    size = int(entry["size"])

    # 文件变小 = 被重写/截断，只能全量重读
    if size < prev_size:
        return 0, True
    # 没变化，跳过
    if size == prev_size and abs(entry["mtime"] - state["file_mtime"]) < 1e-6:
        return -1, False

    # 校验上次读到的最后一条记录是否还在原位，防止通达信重写文件导致数据错位
    if prev_count > 0:
        probe = tr.read_record_at(entry["path"], prev_count - 1)
        if probe is None or probe["trade_date"] != int(state["last_date"]):
            return 0, True

    return prev_count * tr.DAY_RECORD_SIZE, False


def sync_daily(
    cfg: dict,
    full: bool = False,
    codes: Optional[List[str]] = None,
    verbose: bool = True,
) -> dict:
    """执行一次增量同步，返回统计信息。"""
    paths = resolve_paths(cfg)
    data_cfg = cfg.get("data", {})
    include_bj = bool(data_cfg.get("include_bj", False))
    markets = tuple(data_cfg.get("markets", ["sh", "sz"] + (["bj"] if include_bj else [])))

    t0 = time.time()
    entries = tr.scan_day_files(paths, markets=markets)
    if not entries:
        raise SystemExit(f"在 {paths.vipdoc} 下没有找到任何 .day 文件，请检查路径。")

    # 只留可交易个股 + 主要指数。
    # 注意：上证指数是 sh000001，平安银行是 sz000001，6 位代码会撞车。
    # 所以指数一律用 "市场+代码" 作为 key（sh000001），个股保持 6 位。
    wanted = []
    for e in entries:
        prefixed = f"{e['market']}{e['code']}"
        if tr.is_tradable_stock(e["code"], e["market"], include_bj=include_bj):
            e["key"] = e["code"]
            e["is_index"] = False
            wanted.append(e)
        elif prefixed in tr.MAJOR_INDEXES:
            e["key"] = prefixed
            e["is_index"] = True
            wanted.append(e)
    if codes:
        keep = {str(c).zfill(6) for c in codes} | set(codes)
        wanted = [e for e in wanted if e["key"] in keep or e["code"] in keep]

    store = BarStore(db_path(cfg))
    states = {} if full else store.get_all_sync_states()

    total_new = 0
    touched = 0
    skipped = 0
    failed: List[str] = []
    batch: List[Tuple[str, "object"]] = []
    state_batch: List[tuple] = []

    for i, entry in enumerate(wanted, 1):
        code = entry["key"]
        try:
            start_offset, _ = _plan_read(entry, states.get(code), full)
            if start_offset < 0:
                skipped += 1
                continue

            df = tr.read_day_file(entry["path"], start_offset=start_offset)
            if df.empty:
                skipped += 1
                continue

            batch.append((code, df))
            total_new += len(df)
            touched += 1

            record_count = entry["size"] // tr.DAY_RECORD_SIZE
            state_batch.append(
                (
                    code,
                    entry["market"],
                    entry["size"],
                    entry["mtime"],
                    record_count,
                    int(df["trade_date"].iloc[-1]),
                )
            )
        except Exception as exc:  # 单只失败不该中断整轮同步
            failed.append(f"{code}: {exc}")
            continue

        if len(batch) >= BATCH_SIZE:
            store.upsert_bars_bulk(batch)
            store.set_sync_states_bulk(state_batch)
            batch, state_batch = [], []
            if verbose:
                print(f"  ... {i}/{len(wanted)} 已处理，新增 {total_new} 条", flush=True)

    if batch:
        store.upsert_bars_bulk(batch)
        store.set_sync_states_bulk(state_batch)

    # 记录 security 表（名称留给 names.py 补，这里先保证 code/market/board 有值）
    store.upsert_securities(
        [
            (
                e["key"],
                tr.MAJOR_INDEXES.get(e["key"], "") if e["is_index"] else "",
                e["market"],
                "INDEX" if e["is_index"] else _board_of(e["code"]),
            )
            for e in wanted
        ]
    )
    store.set_meta("last_sync", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    elapsed = time.time() - t0
    stats = {
        "scanned": len(wanted),
        "updated": touched,
        "skipped": skipped,
        "new_bars": total_new,
        "failed": len(failed),
        "elapsed_sec": round(elapsed, 2),
    }
    if verbose:
        print(
            f"\n同步完成：扫描 {stats['scanned']} 只，更新 {stats['updated']} 只，"
            f"跳过 {stats['skipped']} 只，新增 {stats['new_bars']} 条K线，"
            f"耗时 {stats['elapsed_sec']}s"
        )
        if failed:
            print(f"失败 {len(failed)} 只，前 5 条：")
            for line in failed[:5]:
                print("  " + line)
        db_stats = store.stats()
        print(
            f"库内合计：{db_stats['codes']} 只 / {db_stats['bars']:,} 条 / "
            f"{db_stats['db_size_mb']} MB / 区间 {db_stats['date_min']}~{db_stats['date_max']}"
        )
        if db_stats["named"] == 0:
            print("提示：股票名称表为空，ST 过滤不会生效。跑一次 --names 补上。")
    store.close()
    return stats


def _board_of(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("688"):
        return "STAR"          # 科创板
    if code.startswith(("300", "301")):
        return "CHINEXT"       # 创业板
    if code.startswith(("43", "83", "87", "92", "82")):
        return "BSE"           # 北交所
    return "MAIN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="忽略增量偏移，全量重建")
    parser.add_argument("--names", action="store_true", help="同时更新股票名称")
    parser.add_argument("--xdxr", action="store_true", help="同时更新除权除息数据")
    parser.add_argument("--codes", nargs="*", help="只同步指定代码")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    verbose = not args.quiet

    if verbose:
        print("=== 同步通达信本地日线 ===")
    try:
        sync_daily(cfg, full=args.full, codes=args.codes, verbose=verbose)
    except SystemExit:
        raise
    except FileNotFoundError as exc:
        # 路径没配对是最常见的首次使用问题，给清楚的指引而不是 traceback
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(
            f"\n没有权限读取通达信数据目录：{exc}\n"
            "如果通达信正在运行，通常不影响读取；若报错请以管理员身份重试。",
            file=sys.stderr,
        )
        return 1

    if args.names:
        from zhixing_quant.data.names import update_names

        if verbose:
            print("\n=== 更新股票名称 ===")
        update_names(cfg, verbose=verbose)

    if args.xdxr:
        from zhixing_quant.data.xdxr import update_xdxr

        if verbose:
            print("\n=== 更新除权除息 ===")
        update_xdxr(cfg, verbose=verbose)

    return 0


if __name__ == "__main__":
    sys.exit(main())
