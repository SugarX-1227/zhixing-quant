"""指南针「活跃市值」(0AMV) 指标：从本地指南针数据文件提取入库。

数据来源与格式（2026-09 在指南针全赢决策系统 WavMain 上逆向确认）：

    <指南针目录>/WavMain/ANALYSE/Data/ChinaStk/Z_SK/day.vdat

文件内按年分块存储，块头是 8 字节 ASCII "Z_SK0AMV" + 24 字节 0x00，
之后紧跟 250 条（最后一块可不满）日记录，每条 28 字节：

    trade_date  u32  YYYYMMDD
    open/high/low/close  f32 x4   活跃市值点位
    vol/amount           f32 x2   两市总成交量(股) / 两市股票总成交额(元)

注意：
- 指南针运行期间 day.vdat 被独占锁定，同步前必须先退出指南针。
- 当天的活跃市值 bar 在收盘后由指南针写入，盘中同步拿不到当天数据。
- 活跃市值是指南针专有算法，本模块只搬运数值，不复算公式。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Optional

from zhixing_quant.config import load_config

BLOCK_TAG = b"Z_SK0AMV"
HEADER_LEN = 32          # 8 字节 tag + 24 字节 0x00
RECORD_LEN = 28          # u32 date + 6 x f32
RECS_PER_BLOCK = 250
_DATE_LO, _DATE_HI = 19900101, 20341231


def compass_chinastk_dir(cfg: Optional[dict] = None) -> Path:
    """定位指南针 ChinaStk 数据目录：先看配置，再扫常见盘符。"""
    cfg = cfg or load_config()
    configured = cfg.get("data", {}).get("compass_dir", "")
    if configured:
        p = Path(configured)
        if (p / "Z_SK").is_dir():
            return p
        raise FileNotFoundError(
            f"配置的指南针数据目录不对：{configured}（下面应能找到 Z_SK/day.vdat）"
        )
    for drive in ("C", "D", "E", "F"):
        for candidate in Path(f"{drive}:/").glob("*/WavMain/ANALYSE/Data/ChinaStk"):
            if (candidate / "Z_SK").is_dir():
                return candidate
    raise FileNotFoundError(
        "没找到指南针数据目录（*/WavMain/ANALYSE/Data/ChinaStk）。"
        "请在 config/settings.yaml 的 data.compass_dir 里写明安装路径。"
    )


def read_compass_oamv(day_vdat: Path) -> List[tuple]:
    """解析 day.vdat，返回 (trade_date, open, high, low, close, vol, amount) 列表。

    同一交易日出现多行时（文件里有个暂存槽存当日 forming bar，可能只有部分
    字段非零），保留 OHLC 完整度最高的那条。
    """
    data = Path(day_vdat).read_bytes()
    by_date = {}
    pos = 0
    while True:
        start = data.find(BLOCK_TAG, pos)
        if start < 0:
            break
        pos = start + len(BLOCK_TAG)
        p = start + HEADER_LEN
        block_end = min(p + RECS_PER_BLOCK * RECORD_LEN, len(data))
        while p + RECORD_LEN <= block_end:
            (d,) = struct.unpack_from("<I", data, p)
            if not (_DATE_LO <= d <= _DATE_HI):
                break
            o, h, l, c, vol, amt = struct.unpack_from("<6f", data, p + 4)
            if o == 0.0 and h == 0.0 and l == 0.0 and c == 0.0:
                p += RECORD_LEN
                continue
            row = (int(d), round(o, 2), round(h, 2), round(l, 2), round(c, 2),
                   float(vol), float(amt))
            prev = by_date.get(d)
            # 暂存槽里的 forming bar 可能只有 close 字段有值，完整度低于正式块
            complete = sum(1 for v in row[1:5] if v != 0.0)
            prev_complete = (sum(1 for v in prev[1:5] if v != 0.0)
                             if prev is not None else -1)
            if complete > prev_complete:
                by_date[d] = row
            p += RECORD_LEN
    return [by_date[d] for d in sorted(by_date)]


def sync_oamv(cfg: Optional[dict] = None, verbose: bool = True) -> int:
    """从指南针 day.vdat 增量导入活跃市值到 market.db，返回库内总行数。"""
    cfg = cfg or load_config()
    from zhixing_quant.data.store import BarStore
    from zhixing_quant.data.sync import db_path

    vdat = compass_chinastk_dir(cfg) / "Z_SK" / "day.vdat"
    try:
        rows = read_compass_oamv(vdat)
    except PermissionError as exc:
        raise RuntimeError(
            f"读取指南针数据失败（文件被占用）：{vdat}\n"
            "请先完全退出指南针软件再同步。"
        ) from exc
    if not rows:
        raise RuntimeError(f"{vdat} 里没有解析到任何 0AMV 数据，格式可能变了。")

    store = BarStore(db_path(cfg))
    try:
        known = {
            r["trade_date"] for r in
            store.conn.execute("SELECT trade_date FROM oamv_daily").fetchall()
        }
        fresh = [r for r in rows if r[0] not in known]
        store.upsert_oamv(fresh)
        total = store.conn.execute(
            "SELECT COUNT(*) AS n FROM oamv_daily").fetchone()["n"]
        latest = store.conn.execute(
            "SELECT MAX(trade_date) AS d FROM oamv_daily").fetchone()["d"]
    finally:
        store.close()

    if verbose:
        print(
            f"  活跃市值：文件含 {len(rows)} 条，新增 {len(fresh)} 条，"
            f"库内共 {total} 条（{rows[0][0]} ~ {latest}）"
        )
    return total


def load_oamv(
    start_date: Optional[int] = None, end_date: Optional[int] = None,
):
    """从 market.db 读活跃市值日线 DataFrame（trade_date 升序）。

    只返回 trade_date <= end_date 的行，供择时做时点判断，避免未来函数。
    """
    from zhixing_quant.data.tdx_loader import get_store

    store = get_store()
    return store.load_oamv(start_date=start_date, end_date=end_date)
