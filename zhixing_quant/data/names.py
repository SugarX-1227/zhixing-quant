"""股票名称解析。

名称是 ST 过滤唯一依赖的字段，但通达信本地的名称文件格式随版本变化很大，
所以这里按可靠性排序尝试三条路，任意一条成功即可：

1. data/stock_names.csv        —— 你自己维护的 code,name 两列，最稳，离线可用
2. pytdx get_security_list     —— 连通达信自家行情服务器，免费，一次几十个请求
3. T0002/hq_cache/*.tnf        —— 纯本地解析，无依赖，但格式随版本漂移，尽力而为

三条都失败时名称留空，程序不会崩，但 ST 过滤会失效并给出告警。
"""

from __future__ import annotations

import csv
import struct
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from zhixing_quant.config import PROJECT_ROOT
from zhixing_quant.data import tdx_reader as tr
from zhixing_quant.data.store import BarStore


def update_names(cfg: dict, verbose: bool = True) -> int:
    """把股票名称写进 security 表，返回成功解析的数量。"""
    from zhixing_quant.data.sync import db_path, resolve_paths

    names: Dict[str, str] = {}

    csv_path = PROJECT_ROOT / "data" / "stock_names.csv"
    if csv_path.exists():
        names.update(_from_csv(csv_path))
        if verbose:
            print(f"  从 {csv_path.name} 读到 {len(names)} 条")

    # 在线列表可能不完整；本地 TNF 是通达信当前安装里的完整证券名表，
    # 先读本地，再用 pytdx 补充本地没有的代码。
    try:
        paths = resolve_paths(cfg)
        got = _from_tnf(paths.vipdoc.parent)
        for code, name in got.items():
            names.setdefault(code, name)
        if verbose and got:
            print(f"  从本地 .tnf 解析到 {len(got)} 条")
    except Exception as exc:
        if verbose:
            print(f"  本地 .tnf 解析失败：{exc}")

    got = _from_pytdx(verbose=verbose)
    for code, name in got.items():
        names.setdefault(code, name)

    if not names:
        if verbose:
            print(
                "  ⚠ 没能拿到任何股票名称。ST 过滤将失效。\n"
                "    解决办法二选一：\n"
                "      a) pip install pytdx 后重跑 --names\n"
                "      b) 手工准备 data/stock_names.csv，两列 code,name"
            )
        return 0

    store = BarStore(db_path(cfg))
    records: List[Tuple[str, str, str, str]] = []
    existing = {r["code"]: r for _, r in store.load_securities().iterrows()}
    for code, name in names.items():
        code = str(code).zfill(6)
        prev = existing.get(code)
        market = prev["market"] if prev is not None else tr.guess_market(code)
        board = prev["board"] if prev is not None else ""
        records.append((code, name, market, board))
    store.upsert_securities(records)
    as_of = store.market_date() or int(datetime.now().strftime("%Y%m%d"))
    store.record_name_history([(code, name) for code, name, _market, _board in records], as_of)
    store.close()

    if verbose:
        print(f"  写入 {len(records)} 条股票名称（历史快照 {as_of}）")
    return len(records)


def _from_csv(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if len(row) < 2:
                continue
            code = row[0].strip().zfill(6)
            name = row[1].strip()
            if code.isdigit() and name and name.lower() != "name":
                out[code] = name
    return out


def _from_pytdx(verbose: bool = True) -> Dict[str, str]:
    """用 pytdx 拉全市场证券列表。pytdx 走的是通达信自家免费行情端口。"""
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        if verbose:
            print("  未安装 pytdx，跳过在线名称获取（pip install pytdx）")
        return {}

    out: Dict[str, str] = {}
    api = TdxHq_API(heartbeat=False)
    servers = [
        ("119.147.212.81", 7709),
        ("218.108.98.244", 7709),
        ("123.125.108.14", 7709),
    ]
    for host, port in servers:
        try:
            with api.connect(host, port, time_out=8):
                for market in (0, 1):  # 0=深, 1=沪
                    start = 0
                    while True:
                        chunk = api.get_security_list(market, start)
                        if not chunk:
                            break
                        for item in chunk:
                            code = str(item["code"]).zfill(6)
                            out[code] = str(item["name"]).strip()
                        if len(chunk) < 1000:
                            break
                        start += len(chunk)
            if out:
                if verbose:
                    print(f"  pytdx({host}) 返回 {len(out)} 条")
                return out
        except Exception as exc:
            if verbose:
                print(f"  pytdx 连接 {host} 失败：{exc}")
            continue
    return out


def _from_tnf(install_dir: Path) -> Dict[str, str]:
    """尽力解析 T0002/hq_cache/*.tnf。格式随通达信版本变化，失败返回空 dict。"""
    out: Dict[str, str] = {}
    cache_dir = install_dir / "T0002" / "hq_cache"
    if not cache_dir.is_dir():
        return out

    for fname in ("shs.tnf", "szs.tnf", "bjs.tnf", "shex.tnf", "szex.tnf", "bjex.tnf"):
        f = cache_dir / fname
        if not f.exists():
            continue
        try:
            data = f.read_bytes()
        except OSError:
            continue
        # 当前通达信布局：50 字节头 + 360 字节定长记录，code 在 offset 0，name 在 offset 30。
        header, rec, name_offset = 50, 360, 30
        if len(data) <= header:
            continue
        for off in range(header, len(data) - rec + 1, rec):
            block = data[off : off + rec]
            code = block[0:6].decode("gbk", errors="ignore").strip("\x00 ").strip()
            if not (len(code) == 6 and code.isdigit()):
                continue
            name = block[name_offset:name_offset + 16].decode("gbk", errors="ignore").strip("\x00 ").strip()
            if name:
                out[code] = name
    return out


def export_template(path: Path = None) -> Path:
    """导出一个 stock_names.csv 模板，方便手工维护。"""
    path = path or (PROJECT_ROOT / "data" / "stock_names.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("code,name\n600000,浦发银行\n000001,平安银行\n", encoding="utf-8")
    return path
