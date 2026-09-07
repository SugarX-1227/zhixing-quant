"""通达信本地行情文件二进制解析器。

通达信客户端会把下载到的历史行情落在安装目录的 vipdoc 下：

    vipdoc/sh/lday/sh600000.day      日线，32 字节定长
    vipdoc/sz/lday/sz000001.day
    vipdoc/bj/lday/bj430047.day
    vipdoc/sh/minline/sh600000.lc1   1 分钟线，32 字节定长
    vipdoc/sh/fzline/sh600000.lc5    5 分钟线，32 字节定长

文件没有加密，只是私有二进制格式。因为是定长记录，可以按字节偏移做增量读取：
记住上次读到第几条，下次从 offset = n * 32 开始 seek 即可。

.day 记录布局（小端）:
    0-3    uint32   日期 YYYYMMDD
    4-7    uint32   开盘价 * 100
    8-11   uint32   最高价 * 100
    12-15  uint32   最低价 * 100
    16-19  uint32   收盘价 * 100
    20-23  float32  成交额（元）
    24-27  uint32   成交量（股）
    28-31  uint32   保留（部分版本为上日收盘）

.lc1 / .lc5 记录布局（小端）:
    0-1    uint16   日期编码，year = v // 2048 + 2004
    2-3    uint16   分钟数（自 00:00 起）
    4-7    float32  开盘
    8-11   float32  最高
    12-15  float32  最低
    16-19  float32  收盘
    20-23  float32  成交额
    24-27  uint32   成交量
    28-31  uint32   保留
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

import pandas as pd

DAY_RECORD_SIZE = 32
MIN_RECORD_SIZE = 32

_DAY_STRUCT = struct.Struct("<IIIIIfII")
_MIN_STRUCT = struct.Struct("<HHfffffI")

# 指数使用不同的价格精度约定，且这些前缀走指数目录
INDEX_PREFIXES = ("000", "399", "899")


@dataclass(frozen=True)
class TdxPaths:
    """通达信 vipdoc 目录下的各类数据路径。"""

    vipdoc: Path

    @classmethod
    def from_install_dir(cls, install_dir: str | Path) -> "TdxPaths":
        """从通达信安装目录（或直接给 vipdoc 目录）构造。"""
        root = Path(install_dir).expanduser().resolve()
        vipdoc = root if root.name.lower() == "vipdoc" else root / "vipdoc"
        if not vipdoc.is_dir():
            raise FileNotFoundError(
                f"找不到 vipdoc 目录: {vipdoc}\n"
                "请把 config/settings.yaml 里的 data.tdx_install_dir 指向通达信安装目录，"
                "例如 C:/new_tdx 或 /Users/you/tdx。"
            )
        return cls(vipdoc=vipdoc)

    def lday_dir(self, market: str) -> Path:
        return self.vipdoc / market / "lday"

    def day_file(self, market: str, code: str) -> Path:
        return self.lday_dir(market) / f"{market}{code}.day"

    def minute_file(self, market: str, code: str, freq: str = "lc1") -> Path:
        sub = "minline" if freq == "lc1" else "fzline"
        return self.vipdoc / market / sub / f"{market}{code}.{freq}"


def guess_market(code: str) -> str:
    """根据 6 位代码推断市场目录名 (sh / sz / bj)。

    只覆盖个股 + 常见指数，够选股用；有歧义时由 store 里记录的真实 market 覆盖。
    """
    code = str(code).zfill(6)
    if code.startswith(("60", "688", "510", "511", "110", "113", "000001")):
        return "sh"
    if code.startswith(("43", "83", "87", "92", "82")):
        return "bj"
    return "sz"


def iter_day_records(
    path: Path,
    start_offset: int = 0,
    price_divisor: float = 100.0,
) -> Iterator[dict]:
    """从 .day 文件的指定字节偏移开始，逐条 yield 记录。

    Args:
        path: .day 文件路径。
        start_offset: 起始字节偏移，必须是 32 的整数倍。
        price_divisor: 价格除数，个股为 100。

    Yields:
        dict，键为 trade_date/open/high/low/close/amount/vol。
    """
    if start_offset % DAY_RECORD_SIZE != 0:
        raise ValueError(f"start_offset 必须是 {DAY_RECORD_SIZE} 的整数倍，收到 {start_offset}")

    with path.open("rb") as fh:
        fh.seek(start_offset)
        while True:
            chunk = fh.read(DAY_RECORD_SIZE * 2000)
            if not chunk:
                return
            usable = len(chunk) - (len(chunk) % DAY_RECORD_SIZE)
            for off in range(0, usable, DAY_RECORD_SIZE):
                date_i, o, h, low, c, amount, vol, _ = _DAY_STRUCT.unpack_from(chunk, off)
                # 明显损坏的记录直接跳过，不要污染数据库
                if not (19900101 <= date_i <= 21001231):
                    continue
                yield {
                    "trade_date": date_i,
                    "open": o / price_divisor,
                    "high": h / price_divisor,
                    "low": low / price_divisor,
                    "close": c / price_divisor,
                    "amount": float(amount),
                    "vol": float(vol),
                }


def read_day_file(
    path: Path,
    start_offset: int = 0,
    price_divisor: float = 100.0,
) -> pd.DataFrame:
    """读取 .day 文件为 DataFrame（可从偏移量增量读取）。"""
    rows = list(iter_day_records(path, start_offset=start_offset, price_divisor=price_divisor))
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "open", "high", "low", "close", "amount", "vol"]
        )
    return pd.DataFrame(rows)


def read_last_day_record(path: Path, price_divisor: float = 100.0) -> Optional[dict]:
    """只读文件最后一条记录，用于校验增量读取的起点是否仍然有效。"""
    size = path.stat().st_size
    if size < DAY_RECORD_SIZE:
        return None
    offset = size - (size % DAY_RECORD_SIZE) - DAY_RECORD_SIZE
    if offset < 0:
        return None
    with path.open("rb") as fh:
        fh.seek(offset)
        buf = fh.read(DAY_RECORD_SIZE)
    if len(buf) < DAY_RECORD_SIZE:
        return None
    date_i, o, h, low, c, amount, vol, _ = _DAY_STRUCT.unpack(buf)
    return {
        "trade_date": date_i,
        "open": o / price_divisor,
        "high": h / price_divisor,
        "low": low / price_divisor,
        "close": c / price_divisor,
        "amount": float(amount),
        "vol": float(vol),
    }


def read_record_at(path: Path, index: int, price_divisor: float = 100.0) -> Optional[dict]:
    """读取第 index 条记录（0 起），用于增量校验。"""
    offset = index * DAY_RECORD_SIZE
    if offset < 0 or offset + DAY_RECORD_SIZE > path.stat().st_size:
        return None
    with path.open("rb") as fh:
        fh.seek(offset)
        buf = fh.read(DAY_RECORD_SIZE)
    date_i, o, h, low, c, amount, vol, _ = _DAY_STRUCT.unpack(buf)
    return {
        "trade_date": date_i,
        "open": o / price_divisor,
        "high": h / price_divisor,
        "low": low / price_divisor,
        "close": c / price_divisor,
        "amount": float(amount),
        "vol": float(vol),
    }


def read_minute_file(path: Path, start_offset: int = 0) -> pd.DataFrame:
    """读取 .lc1 / .lc5 分钟线文件。"""
    rows: List[dict] = []
    with path.open("rb") as fh:
        fh.seek(start_offset)
        data = fh.read()
    usable = len(data) - (len(data) % MIN_RECORD_SIZE)
    for off in range(0, usable, MIN_RECORD_SIZE):
        d, m, o, h, low, c, amount, vol = _MIN_STRUCT.unpack_from(data, off)
        year = d // 2048 + 2004
        month = (d % 2048) // 100
        day = (d % 2048) % 100
        hour, minute = divmod(m, 60)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        rows.append(
            {
                "datetime": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00",
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "amount": float(amount),
                "vol": float(vol),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "amount", "vol"]
        )
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def scan_day_files(paths: TdxPaths, markets: tuple = ("sh", "sz", "bj")) -> List[dict]:
    """枚举所有可用的 .day 文件。

    Returns:
        [{"code": "600000", "market": "sh", "path": Path, "size": int, "mtime": float}, ...]
    """
    found: List[dict] = []
    for market in markets:
        d = paths.lday_dir(market)
        if not d.is_dir():
            continue
        for f in d.glob(f"{market}*.day"):
            code = f.stem[len(market):]
            if len(code) != 6 or not code.isdigit():
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            if stat.st_size < DAY_RECORD_SIZE:
                continue
            found.append(
                {
                    "code": code,
                    "market": market,
                    "path": f,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
    return found


MAJOR_INDEXES = {
    "sh000001": "上证指数",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
}


def is_tradable_stock(code: str, market: str, include_bj: bool = False) -> bool:
    """过滤出可交易的 A 股个股，剔除指数、基金、债券、权证等。"""
    code = str(code).zfill(6)
    if market == "sh":
        # 60 主板，688 科创板
        return code.startswith("60") or code.startswith("688")
    if market == "sz":
        # 000/001 主板，002/003 中小板，300/301 创业板
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return include_bj and code.startswith(("43", "83", "87", "92", "82"))
    return False
