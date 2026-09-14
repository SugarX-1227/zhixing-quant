"""股票池构建（时点正确 / point-in-time）。

为什么单独抽一个模块：

回测页原来这样建池：

    uni = filter_universe(fetch_a_spot(), cfg).head(pool)

`fetch_a_spot()` 不带参数 = 取库里**最新**交易日。也就是说回测 2024-09→2026-09
时，池子是"2026-09-09 成交额前 200 名"。2024 年你不可能知道两年后谁的成交额最大，
而且退市股、变 ST 的、成交萎缩的全部被自动剔除——剩下的都是赢家。

这是前视偏差叠加幸存者偏差，会系统性地把回测收益抬高，且抬多少无法估计。

本模块的 build_universe 强制传入 as_of 日期，只用那一天及之前的信息选股：
- 成交额、涨跌取 as_of 当天的快照
- ST / 退市只用 as_of 当时可知的名称；没有名称历史时宁可不滤，也不拿今天的名字前视
- 上市天数按 as_of 之前的 K 线根数算，不是全库总根数
- 板块归属是静态属性，不受时点影响

另外提供 rebalance_dates，支持按季度滚动重建池子，比固定池更接近真实操作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import pandas as pd

# security.board 的取值，见 data/sync.py::_board_of
BOARDS = {
    "MAIN": "沪深主板",
    "CHINEXT": "创业板",
    "STAR": "科创板",
    "BSE": "北交所",
}
DEFAULT_BOARDS = ("MAIN", "CHINEXT")


@dataclass
class UniverseSpec:
    """建池条件。回测和选股共用，保证两边口径一致。"""

    boards: Sequence[str] = DEFAULT_BOARDS
    size: Optional[int] = 200            # None = 不截断
    min_amount: float = 1e8              # 日成交额下限
    exclude_st: bool = True
    min_listed_bars: int = 120           # 上市至少多少根K线，剔除次新
    rank_by: str = "amount"              # amount / pct_chg / random
    include_codes: Sequence[str] = field(default_factory=tuple)   # 强制纳入

    def describe(self) -> str:
        names = "、".join(BOARDS.get(b, b) for b in self.boards)
        parts = [names]
        if self.min_amount:
            parts.append(f"成交额≥{self.min_amount / 1e8:.1f}亿")
        if self.exclude_st:
            parts.append("剔除ST")
        if self.min_listed_bars:
            parts.append(f"上市≥{self.min_listed_bars}根K线")
        if self.size:
            parts.append(f"取前{self.size}只")
        return " · ".join(parts)


@dataclass
class UniverseResult:
    codes: List[str]
    frame: pd.DataFrame
    as_of: int
    spec: UniverseSpec
    dropped: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        d = "、".join(f"{k} {v} 只" for k, v in self.dropped.items() if v)
        return (f"按 {self.as_of} 收盘数据建池，最终 {len(self.codes)} 只"
                + (f"（剔除：{d}）" if d else ""))


def build_universe(
    cfg: dict,
    as_of: Optional[str] = None,
    spec: Optional[UniverseSpec] = None,
) -> UniverseResult:
    """按 as_of 当天可得的信息建池。

    Args:
        cfg: 配置字典。
        as_of: YYYYMMDD。**回测必须传**，传 None 只适用于当日选股。
        spec: 建池条件，None 时从 cfg.universe 读默认值。

    Returns:
        UniverseResult
    """
    from zhixing_quant.data.tdx_loader import fetch_a_spot, get_store

    spec = spec or spec_from_config(cfg)
    spot = fetch_a_spot(trade_date=as_of)
    trade_date = int(spot["date"].iloc[0].strftime("%Y%m%d"))

    df = spot.copy()
    dropped: Dict[str, int] = {}
    warnings: List[str] = []

    def cut(mask, label):
        nonlocal df
        before = len(df)
        df = df[mask]
        n = before - len(df)
        if n:
            dropped[label] = dropped.get(label, 0) + n

    store = get_store()
    boards = store.load_securities().set_index("code")["board"].to_dict()
    keep = set(spec.boards)
    cut(df["code"].map(lambda c: boards.get(c, "MAIN")).isin(keep), "板块不符")

    if spec.exclude_st:
        from zhixing_quant.data.tdx_loader import names_for_st_filter, st_like
        name_map, st_warn = names_for_st_filter(store, trade_date)
        if st_warn:
            warnings.append(st_warn)
        if name_map:
            names = df["code"].map(lambda c: name_map.get(str(c), "")).astype(str)
            df = df.copy()
            df["name"] = names
            cut(~names.map(st_like), "ST/退市")

    if spec.min_amount:
        cut(df["amount"] >= float(spec.min_amount), "成交额不足")

    cut(df["close"] > 0, "无有效价格")

    # 上市天数：只数 as_of 之前的 K 线，不能用全库总数（那是未来信息）
    if spec.min_listed_bars:
        counts = pd.read_sql_query(
            "SELECT code, COUNT(*) AS n FROM daily_bar WHERE trade_date <= ? "
            "GROUP BY code",
            store.conn, params=[trade_date],
        ).set_index("code")["n"].to_dict()
        cut(df["code"].map(lambda c: counts.get(c, 0)) >= int(spec.min_listed_bars),
            "次新股")

    if spec.rank_by == "random":
        df = df.sample(frac=1.0, random_state=42)
    elif spec.rank_by in df.columns:
        df = df.sort_values(spec.rank_by, ascending=False)
    df = df.reset_index(drop=True)

    if spec.size:
        df = df.head(int(spec.size))

    forced = [c for c in spec.include_codes if c not in set(df["code"])]
    if forced:
        extra = spot[spot["code"].isin(forced)]
        df = pd.concat([df, extra], ignore_index=True)

    return UniverseResult(codes=df["code"].astype(str).tolist(), frame=df,
                          as_of=trade_date, spec=spec, dropped=dropped,
                          warnings=warnings)


def spec_from_config(cfg: dict) -> UniverseSpec:
    u = cfg.get("universe", {}) or {}
    exclude = set(u.get("exclude_boards", []) or [])
    boards = tuple(b for b in DEFAULT_BOARDS if b not in exclude) or DEFAULT_BOARDS
    return UniverseSpec(
        boards=boards,
        size=int(u.get("max_candidates", 0)) or None,
        min_amount=float(u.get("min_daily_amount", 0) or 0),
        exclude_st=bool(u.get("exclude_st", True)),
        min_listed_bars=int(u.get("exclude_new_stock_days", 0) or 0),
    )


def rebalance_dates(start: str, end: str, months: int = 3) -> List[str]:
    """滚动重建池子的日期序列。

    固定池（只在开始日建一次）仍有轻微偏差：整段期间都持有同一批标的，
    而现实中你会定期调整关注范围。按季度滚动更接近真实操作。
    """
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    out, cur = [], lo
    while cur <= hi:
        out.append(cur.strftime("%Y%m%d"))
        cur = cur + pd.DateOffset(months=months)
    return out
