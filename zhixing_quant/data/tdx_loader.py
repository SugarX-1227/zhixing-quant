"""数据访问层：接口与原来的 MCP 版本完全一致，底层换成本地 SQLite。

对上层（scanner / streamlit / backtest）来说这是一个 drop-in 替换：
    fetch_a_spot(cache=True)                       -> 全市场快照
    filter_universe(spot, cfg)                     -> 过滤后的股票池
    load_daily(code, start_date, end_date, adjust) -> 单只日线

所以 scanner/daily_brick.py 等文件一行都不用改。
区别只是：不再有任何网络请求和 MCP 调用次数消耗。
"""

from __future__ import annotations

import functools
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from zhixing_quant.config import PROJECT_ROOT, load_config
from zhixing_quant.data.store import BarStore
from zhixing_quant.data.xdxr import apply_qfq

_STORE: Optional[BarStore] = None
_CFG: Optional[dict] = None


class DataNotReady(RuntimeError):
    """本地库还没建好时抛出，带上可执行的修复指引。"""


def _cfg() -> dict:
    global _CFG
    if _CFG is None:
        _CFG = load_config()
    return _CFG


def set_config(cfg: dict) -> None:
    """允许上层注入配置（Streamlit 里切换配置时用）。"""
    global _CFG, _STORE
    _CFG = cfg
    if _STORE is not None:
        _STORE.close()
        _STORE = None


def get_store() -> BarStore:
    """返回进程内共享的 BarStore。"""
    global _STORE
    if _STORE is None:
        cfg = _cfg()
        p = cfg.get("data", {}).get("db_path", "data/market.db")
        path = Path(p)
        path = path if path.is_absolute() else PROJECT_ROOT / path
        if not path.exists():
            raise DataNotReady(
                f"本地行情库不存在：{path}\n"
                "请先跑一次同步：\n"
                "    python -m zhixing_quant.data.sync --names --xdxr"
            )
        _STORE = BarStore(path)
    return _STORE


def reset_store() -> None:
    """关闭并释放连接，同步完数据后调用可以看到最新结果。"""
    global _STORE
    if _STORE is not None:
        _STORE.close()
        _STORE = None


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


def fetch_a_spot(cache: bool = True, trade_date: Optional[str] = None) -> pd.DataFrame:
    """全市场快照，取代原来的 tdx_screener 条件选股。

    Args:
        cache: 保留参数以兼容旧接口，本地库读取本身就没有网络开销。
        trade_date: YYYYMMDD，默认取库里最新交易日。

    Returns:
        DataFrame: code, name, open, high, low, close, amount, vol, pct_chg, date
    """
    store = get_store()
    td = int(trade_date) if trade_date else None
    spot = store.latest_snapshot(td)
    if spot.empty:
        raise DataNotReady(
            "本地行情库里没有数据。请先打开通达信下载完历史行情，再跑：\n"
            "    python -m zhixing_quant.data.sync --names --xdxr"
        )
    return spot


def filter_universe(spot: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """按流动性 / ST / 次新 / 板块过滤股票池，按成交额降序。

    Rule source:
        config/settings.yaml -> universe 段，与旧实现保持一致。
    """
    uni_cfg = cfg.get("universe", {})
    df = spot.copy()

    min_amount = float(uni_cfg.get("min_daily_amount", 0) or 0)
    if min_amount > 0:
        df = df[df["amount"] >= min_amount]

    if uni_cfg.get("exclude_st", True):
        has_names = (df["name"].astype(str).str.len() > 0).any()
        if has_names:
            name = df["name"].astype(str).str.upper()
            df = df[~name.str.contains("ST") & ~name.str.contains("退")]
        # 名称缺失时不做 ST 过滤，由上层提示用户跑 --names

    # 指数永远不进股票池；其余板块由配置决定
    exclude_boards = set(uni_cfg.get("exclude_boards", []) or []) | {"INDEX"}
    store = get_store()
    boards = store.load_securities().set_index("code")["board"].to_dict()
    df = df[~df["code"].map(lambda c: boards.get(c, "MAIN")).isin(exclude_boards)]

    new_days = int(uni_cfg.get("exclude_new_stock_days", 0) or 0)
    if new_days > 0:
        store = get_store()
        counts = pd.read_sql_query(
            "SELECT code, COUNT(*) AS n FROM daily_bar GROUP BY code", store.conn
        ).set_index("code")["n"]
        df = df[df["code"].map(lambda c: counts.get(c, 0)) >= new_days]

    df = df[df["close"] > 0]
    return df.sort_values("amount", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 日线
# ---------------------------------------------------------------------------


def load_daily(
    code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
    cache: bool = True,
) -> pd.DataFrame:
    """读取单只股票日线。

    Args:
        code: 6 位代码。
        start_date / end_date: YYYYMMDD。
        adjust: "qfq" 前复权（默认），"" 不复权。
        cache: 兼容旧接口，无实际作用。

    Returns:
        DatetimeIndex 索引，含 open/high/low/close/amount/vol/pct_chg。
        df.attrs["adjust"] 标明实际使用的复权口径，可能因缺少除权数据退化为 "none"。
    """
    store = get_store()
    code = str(code).zfill(6)
    df = store.load_bars(
        code,
        start_date=int(start_date) if start_date else None,
        end_date=int(end_date) if end_date else None,
    )
    if df.empty:
        return _with_pct_chg(df)

    if adjust == "qfq":
        df = apply_qfq(df, store.load_xdxr(code))
    else:
        df.attrs["adjust"] = "none"
    return _with_pct_chg(df)


def load_daily_many(
    codes: Sequence[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
) -> Dict[str, pd.DataFrame]:
    """批量读取。扫描全市场时用这个，比逐只 load_daily 快一个数量级。"""
    store = get_store()
    codes = [str(c).zfill(6) for c in codes]
    raw = store.load_bars_many(
        codes,
        start_date=int(start_date) if start_date else None,
        end_date=int(end_date) if end_date else None,
    )
    out: Dict[str, pd.DataFrame] = {}
    for code, df in raw.items():
        if adjust == "qfq":
            df = apply_qfq(df, store.load_xdxr(code))
        else:
            df.attrs["adjust"] = "none"
        out[code] = _with_pct_chg(df)
    return out


def _with_pct_chg(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df["pct_chg"] = []
        return df
    out = df.copy()
    out.attrs = dict(df.attrs)
    out["pct_chg"] = (out["close"] / out["close"].shift(1) - 1.0) * 100.0
    return out


def default_date_range(end_date: Optional[str] = None, calendar_days: int = 400) -> tuple:
    """给扫描器用的默认起止日期。

    400 自然日约等于 260 个交易日，足够算 MA114。
    """
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=calendar_days)).strftime("%Y%m%d")
    return start, end


def data_health(cfg: Optional[dict] = None) -> dict:
    """数据体检，Streamlit 首页展示用。"""
    cfg = cfg or _cfg()
    try:
        store = get_store()
    except DataNotReady as exc:
        return {"ok": False, "message": str(exc)}

    stats = store.stats()
    warnings: List[str] = []
    if stats["named"] == 0:
        warnings.append("股票名称表为空，ST 过滤不生效。跑 `--names` 补上。")
    if stats["xdxr_codes"] == 0:
        warnings.append(
            "没有除权除息数据，当前使用不复权价格，除权日附近可能出现假信号。跑 `--xdxr` 补上。"
        )
    if stats["date_max"]:
        latest = datetime.strptime(str(stats["date_max"]), "%Y%m%d")
        lag = (datetime.now() - latest).days
        if lag > 5:
            warnings.append(
                f"最新数据停留在 {stats['date_max']}，已落后 {lag} 天。"
                "打开通达信让它下载完盘后数据，再跑一次同步。"
            )
    return {"ok": True, "warnings": warnings, **stats}
