"""把 market.db 切一片出来，供云端 AI 会话使用。

为什么不用 MCP
--------------

`zhixing_quant/mcp_server.py` 那套在**局域网内**是好用的，但云端 AI 会话
连不上，实测（2026-09）：

1. `10.9.2.237` 是 RFC1918 私有地址，云端容器没有到你局域网的路由。
   而且云端代理的 noProxy 名单里含 `10.0.0.0/8`，私有网段会被当直连
   尝试，结果就是 10 秒超时。
2. 架公网隧道（Cloudflare Tunnel / ngrok）也不行——云端的出网策略是
   白名单，网关对 `trycloudflare.com` / `ngrok.com` 的 CONNECT
   直接回 403。

**唯一稳定通的是 GitHub。** 所以改成：在有数据的机器上导出一个精简库，
作为 GitHub Release 附件上传，云端会话直接下载。

导出什么
--------

只导出行情表，和 MCP 服务的白名单一致：

    daily_bar / oamv_daily / xdxr / security / security_name_hist

个人交易数据（position / trade_log / account / watchlist / blacklist）
**一行都不会进导出文件**——和 MCP 服务同一条纪律。

体积
----

全库 1600 万根 K 线约 2GB，传不动也没必要。按股票池 + 日期范围切片后
通常只有几十 MB：800 只 × 3 年 ≈ 60 万行，VACUUM 后约 15-25MB，
gzip 后更小。GitHub Release 单文件上限 2GB，绰绰有余。

用法
----

    # 默认：按最新交易日成交额取前 800 只，近 3 年
    python scripts/export_slice.py

    # 自定义
    python scripts/export_slice.py --size 1200 --start 20220101 --gzip

    # 指定代码（基准指数记得带上，回测要用）
    python scripts/export_slice.py --codes 600000,000001,sh000300

然后把 `data/market_slice.db.gz` 作为 Release 附件传上去：

    gh release create data-20260918 data/market_slice.db.gz \\
        --title "行情切片 20260918" --notes "800 只 × 2022-2026"
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 与 MCP 服务白名单一致。个人交易数据绝不导出。
EXPORT_TABLES = ("daily_bar", "oamv_daily", "xdxr", "security",
                 "security_name_hist")
NEVER_EXPORT = ("position", "trade_log", "account", "watchlist", "blacklist",
                "sync_state")

# 基准指数默认带上，不然云端跑回测算不了超额收益
DEFAULT_EXTRA = ("sh000300", "sh000905", "sh000001", "sz399001")


# ⚠️ 选择偏差警告（2026-09-20 实测）
# pick_codes 按**某一个近期日期**的成交额降序取前 N 只。能在今天挤进成交额
# 前列的股票，本身就是过去几年跑赢的那批，拿这批票回测过去等于提前知道了
# 答案。同一份代码同一段时间：800 只切片上 B2 总收益 +115.3%，
# 全量 5213 只上 −28.0%，符号都是反的。
# 所以切片只能用来验证代码跑不跑得通，**任何策略结论都必须在
# data-20260918-all（全量）上重跑**。

def pick_codes(src: sqlite3.Connection, size: int, min_amount: float,
               as_of: Optional[int]) -> List[str]:
    """按 as_of 当日成交额取前 N 只。as_of 缺省用库里**最后一个完整交易日**。

    ⚠️ 这里踩过一次坑：原实现直接用 `MAX(trade_date)` 当截面，隐含假设
    最新一天的数据是完整的。实际上同步到一半、或当天只写进了一条记录时，
    那一天只有个位数的股票——选池就只选出 1 只，加上基准共 5 只，
    导出一个 0.5MB 的库，而脚本一声不吭还打印「自查通过」。

    现在：先统计各交易日的记录数，取中位数作为「正常一天应有多少只」，
    从后往前找第一个达到中位数一半的日子当截面；并在选出的只数明显
    不足时**直接报错退出**，而不是导出一个残缺的库。

    Args:
        src: 只读连接。
        size: 想要的只数。
        min_amount: 成交额下限。
        as_of: 指定截面日；缺省自动挑最后一个完整交易日。

    Returns:
        代码列表。

    Raises:
        RuntimeError: 选出的只数不足期望的一半。
    """
    if as_of is None:
        recent = src.execute(
            "SELECT trade_date, COUNT(*) AS n FROM daily_bar "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 60").fetchall()
        if not recent:
            raise RuntimeError("daily_bar 是空的。")
        counts = sorted(r[1] for r in recent)
        typical = counts[len(counts) // 2]
        as_of = next((int(d) for d, n in recent if n >= typical * 0.5),
                     int(recent[0][0]))
        newest = int(recent[0][0])
        if as_of != newest:
            print(f"  最新交易日 {newest} 只有 "
                  f"{dict((int(d), n) for d, n in recent)[newest]} 条记录"
                  f"（正常约 {typical} 条），数据不完整，"
                  f"改用 {as_of} 作为选池截面。")

    rows = src.execute(
        "SELECT code FROM daily_bar WHERE trade_date = ? AND amount >= ? "
        "ORDER BY amount DESC LIMIT ?", (as_of, min_amount, size)).fetchall()
    codes = [r[0] for r in rows]

    if len(codes) < max(1, size // 2):
        raise RuntimeError(
            f"按 {as_of} 的成交额只选出 {len(codes)} 只，远少于期望的 {size} 只。"
            "多半是那天的数据不完整，或者 --min-amount 设得太高。"
            "导出一个残缺的库比报错更糟——那边会拿它跑出一堆看似正常的结论。"
        )
    return codes


def export(db: Path, out: Path, codes: List[str], start: int, end: Optional[int],
           verbose: bool = True) -> Path:
    """把选定代码和日期范围内的行情写进一个新库。"""
    if out.exists():
        out.unlink()
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    try:
        # 原样照搬表结构和索引，免得两边 schema 漂移
        ddl = src.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND tbl_name IN (%s)" % ",".join("?" * len(EXPORT_TABLES)),
            EXPORT_TABLES).fetchall()
        for (stmt,) in ddl:
            dst.execute(stmt)

        marks = ",".join("?" * len(codes))
        date_hi = end if end is not None else 99999999

        n_bars = _copy(
            src, dst, "daily_bar",
            f"SELECT * FROM daily_bar WHERE code IN ({marks}) "
            f"AND trade_date >= ? AND trade_date <= ?",
            (*codes, start, date_hi))
        # 活跃市值是全市场单序列，很小，整段带走（择时要用）
        n_oamv = _copy(src, dst, "oamv_daily",
                       "SELECT * FROM oamv_daily WHERE trade_date >= ?", (start,))
        n_xdxr = _copy(src, dst, "xdxr",
                       f"SELECT * FROM xdxr WHERE code IN ({marks})", tuple(codes))
        n_sec = _copy(src, dst, "security",
                      f"SELECT * FROM security WHERE code IN ({marks})", tuple(codes))
        n_name = _copy(
            src, dst, "security_name_hist",
            f"SELECT * FROM security_name_hist WHERE code IN ({marks})",
            tuple(codes))
        dst.commit()
        dst.execute("VACUUM")
        dst.commit()
    finally:
        src.close()
        dst.close()

    if verbose:
        mb = out.stat().st_size / 1e6
        print(f"  daily_bar          {n_bars:>9,} 行")
        print(f"  oamv_daily         {n_oamv:>9,} 行")
        print(f"  xdxr               {n_xdxr:>9,} 行")
        print(f"  security           {n_sec:>9,} 行")
        print(f"  security_name_hist {n_name:>9,} 行")
        print(f"  => {out}  {mb:.1f} MB")
    return out


def _copy(src, dst, table: str, query: str, params: tuple) -> int:
    rows = src.execute(query, params).fetchall()
    if not rows:
        return 0
    marks = ",".join("?" * len(rows[0]))
    dst.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
    return len(rows)


def verify(out: Path) -> List[str]:
    """导出后自查：个人数据一行都不能在里面。

    这不是形式主义——导出脚本一旦漏筛，交易记录就跟着传到公网 Release 上了，
    而且不可撤回（GitHub 的附件会被缓存和索引）。
    """
    problems = []
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        leaked = names & set(NEVER_EXPORT)
        if leaked:
            problems.append(f"导出文件里出现了不该有的表：{sorted(leaked)}")
        extra = names - set(EXPORT_TABLES)
        if extra:
            problems.append(f"出现了白名单之外的表：{sorted(extra)}")
        if "daily_bar" in names:
            n = conn.execute("SELECT COUNT(*) FROM daily_bar").fetchone()[0]
            if n == 0:
                problems.append("daily_bar 是空的，切片条件可能筛不出东西")
    finally:
        conn.close()
    return problems


def main() -> None:
    from zhixing_quant.config import load_config
    from zhixing_quant.data.sync import db_path

    p = argparse.ArgumentParser(description="导出行情切片供云端 AI 使用")
    p.add_argument("--size", type=int, default=800, help="按成交额取前 N 只")
    p.add_argument("--min-amount", type=float, default=3e7, help="成交额下限")
    p.add_argument("--start", default="20220101", help="起始日 YYYYMMDD")
    p.add_argument("--end", default=None, help="结束日 YYYYMMDD，默认到最新")
    p.add_argument("--codes", default=None, help="逗号分隔，指定后忽略 --size")
    p.add_argument("--as-of", default=None, help="按哪天的成交额排名选池")
    p.add_argument("--out", default="data/market_slice.db")
    p.add_argument("--gzip", action="store_true", help="额外产出 .gz")
    args = p.parse_args()

    cfg = load_config()
    db = Path(db_path(cfg))
    if not db.exists():
        print(f"找不到行情库：{db}", file=sys.stderr)
        raise SystemExit(1)

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out)
    out = out if out.is_absolute() else root / out
    out.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if args.codes:
            codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        else:
            codes = pick_codes(src, args.size, args.min_amount,
                               int(args.as_of) if args.as_of else None)
        for extra in DEFAULT_EXTRA:
            if extra not in codes:
                codes.append(extra)
    finally:
        src.close()

    print(f"源库 {db}（{db.stat().st_size / 1e6:.0f} MB）")
    print(f"切片 {len(codes)} 只 × {args.start}~{args.end or '最新'}")
    t0 = time.time()
    export(db, out, codes, int(args.start),
           int(args.end) if args.end else None)

    problems = verify(out)
    if problems:
        print("\n❌ 自查未通过，已删除导出文件：", file=sys.stderr)
        for line in problems:
            print("   " + line, file=sys.stderr)
        out.unlink(missing_ok=True)
        raise SystemExit(2)
    print(f"  自查通过：只含行情表，无任何交易/持仓数据　（{time.time() - t0:.0f}s）")

    if args.gzip:
        gz = out.with_suffix(out.suffix + ".gz")
        with open(out, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        print(f"  压缩 => {gz}  {gz.stat().st_size / 1e6:.1f} MB")

    print("\n下一步，把它作为 Release 附件传上去：")
    tag = f"data-{time.strftime('%Y%m%d')}"
    name = (out.name + ".gz") if args.gzip else out.name
    print(f'  gh release create {tag} data/{name} \\\n'
          f'      --title "行情切片 {tag}" --notes "{len(codes)} 只 × '
          f'{args.start}~{args.end or "最新"}"')


if __name__ == "__main__":
    main()
