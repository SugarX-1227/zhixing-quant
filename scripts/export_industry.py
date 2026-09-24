"""把通达信的 A 股行业归属导出成 CSV，供云端会话使用。

数据来源（2026-09 在通达信全赢/普通版上确认，不同版本可能有差异）：

    <tdx>/T0002/hq_cache/tdxhy.cfg    股票 -> 行业代码，纯文本，GBK，
                                     每行 6 个竖线分隔字段：
                                     市场(0深/1沪/2北)|代码|T代码|||X代码
    <tdx>/T0002/hq_cache/tdxzs3.cfg   板块定义，GBK，每行：
                                     名称|880指数代码|类型|层级|0|T或X代码
                                     同时提供 T 系和 X 系的 代码->名称 映射

两套行业口径都导出：
    T 系：通达信自己的行业分级。T+4 位是一级（T1001=银行），
          T+6 位是二级（T110201=全国地产），二级代码前 4 位就是一级。
    X 系：更细的口径（平安银行 T=银行、X=股份制银行）。

限制（如实）：
    - 概念板块（逐股归属）本地没有——旧版的 block_gn.dat 在这个版本里
      不存在，找遍安装目录无果，概念归属应该在服务端。
    - 归属是**导出当天**的快照，通达信行业调整后需重新生成。
    - 退市壳（库里名称为空）不在 tdxhy.cfg 里，会被跳过并计数。

用法：

    python scripts/export_industry.py            # -> data/tdx_industry.csv
    python scripts/export_industry.py --out 其他路径.csv

上传云端时附上 sha256（发布方/下载方对账用）：

    sha256sum data/tdx_industry.csv
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zhixing_quant.config import load_config
from zhixing_quant.data.sync import db_path, resolve_paths

A_SHARE_BOARDS = ("MAIN", "CHINEXT", "STAR", "BSE")
MARKET_NAME = {"0": "SZ", "1": "SH", "2": "BJ"}


def hq_cache_dir(cfg: dict) -> Path:
    """通达信 T0002/hq_cache 目录。复用 resolve_paths 的安装目录校验。"""
    root = resolve_paths(cfg).vipdoc.parent          # vipdoc 的上级 = 安装目录
    cache = root / "T0002" / "hq_cache"
    if not (cache / "tdxhy.cfg").is_file():
        raise FileNotFoundError(
            f"找不到 {cache / 'tdxhy.cfg'}——通达信版本可能不同，"
            "行业文件不在 T0002/hq_cache 下。"
        )
    return cache


def parse_zs3(path: Path) -> dict:
    """tdxzs3.cfg -> {T/X代码: 板块名}。"""
    name_of = {}
    for line in path.read_text(encoding="gbk", errors="replace").splitlines():
        parts = line.split("|")
        if len(parts) >= 6 and parts[5][:1] in "TX":
            name_of[parts[5]] = parts[0]
    return name_of


def parse_hy(path: Path) -> dict:
    """tdxhy.cfg -> {股票代码: (市场, T代码, X代码)}。"""
    rows = {}
    for line in path.read_text(encoding="gbk", errors="replace").splitlines():
        parts = line.split("|")
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        rows[parts[1]] = (MARKET_NAME.get(parts[0], parts[0]), parts[2], parts[5])
    return rows


def t_levels(t_code: str, name_of: dict) -> tuple:
    """T 代码 -> (一级行业, 二级行业)。二级代码前 4 位就是一级。"""
    if len(t_code) == 7:                              # T110201
        return name_of.get(t_code[:5], ""), name_of.get(t_code, "")
    return name_of.get(t_code, ""), ""                # T1001 只有一级


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/tdx_industry.csv")
    args = ap.parse_args()

    cfg = load_config()
    cache = hq_cache_dir(cfg)
    name_of = parse_zs3(cache / "tdxzs3.cfg")
    hy = parse_hy(cache / "tdxhy.cfg")
    print(f"tdxzs3.cfg 提供 {len(name_of)} 个 T/X 代码名称；"
          f"tdxhy.cfg 含 {len(hy)} 只证券")

    con = sqlite3.connect(f"file:{db_path(cfg)}?mode=ro", uri=True)
    secs = con.execute(
        "SELECT code, name FROM security WHERE board IN (%s)"
        % ",".join("?" * len(A_SHARE_BOARDS)), A_SHARE_BOARDS).fetchall()
    con.close()

    out, missing = [], []
    for code, name in secs:
        m = hy.get(code)
        if m is None:
            missing.append(code)
            continue
        mkt, t, x = m
        lv1, lv2 = t_levels(t, name_of)
        out.append([code, name, mkt, t, lv1, lv2, x, name_of.get(x, "")])
    out.sort()

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：带 BOM，Excel 双击打开中文不乱码
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "market", "tdx_hy_code", "hy_level1",
                    "hy_level2", "x_code", "x_name"])
        w.writerows(out)

    no_lv1 = sum(1 for r in out if not r[4])
    print(f"写出 {len(out)} 行 -> {path}")
    print(f"自查：库内 A 股 {len(secs)} 只，无行业归属 {len(missing)} 只"
          f"（退市壳），一级行业为空 {no_lv1} 只")
    if no_lv1:
        print("  ⚠ 有 A 股没拿到一级行业，检查 tdxzs3.cfg 是否完整")
    if missing:
        print(f"  跳过样例：{missing[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
