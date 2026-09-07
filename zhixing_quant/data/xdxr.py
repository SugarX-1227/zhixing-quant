"""复权处理。

这是本地方案最容易被忽略的坑：通达信 .day 文件里存的是**不复权原始价格**。
遇到除权除息，价格会凭空跳一个缺口，均线、KDJ、砖型图这类指标全部失真，
在除权日附近会大量产生假信号。

本模块做两件事：
1. update_xdxr —— 拉取除权除息事件存进 SQLite（需要 pytdx，一次几千个请求，几分钟）
2. apply_qfq   —— 用事件表把原始价格换算成前复权价

前复权因子递推（从最新往回）：
    对第 i 个除权事件（送股 bonus/10、配股 rights/10、配股价 rights_px、派息 dividend/10）
    factor_before = (close_before - dividend/10 + rights/10 * rights_px)
                    / (close_before * (1 + bonus/10 + rights/10))
    该事件之前的所有价格乘以 factor_before。

没有 pytdx 时会退化为不复权，并在返回的 DataFrame 上打 attrs["adjust"] = "none"，
调用方可以据此提示用户。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from zhixing_quant.data.store import BarStore


def update_xdxr(cfg: dict, codes: Optional[List[str]] = None, verbose: bool = True) -> int:
    """拉取除权除息数据写入 SQLite。返回写入的事件数。"""
    from zhixing_quant.data.sync import db_path

    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        if verbose:
            print(
                "  未安装 pytdx，无法获取除权除息数据。\n"
                "  pip install pytdx  然后重跑 --xdxr。\n"
                "  在此之前选股会使用不复权价格，除权日附近可能出现假信号。"
            )
        return 0

    store = BarStore(db_path(cfg))
    if codes is None:
        secs = store.load_securities()
        codes = [c for c in secs["code"].tolist()]

    api = TdxHq_API(heartbeat=True)
    servers = [("119.147.212.81", 7709), ("218.108.98.244", 7709)]
    records: List[Tuple] = []
    ok, fail = 0, 0

    connected = False
    for host, port in servers:
        try:
            api.connect(host, port, time_out=10)
            connected = True
            break
        except Exception:
            continue
    if not connected:
        if verbose:
            print("  所有 pytdx 服务器连接失败，跳过除权除息更新。")
        store.close()
        return 0

    try:
        for i, code in enumerate(codes, 1):
            market = 1 if str(code).startswith(("6", "5")) else 0
            try:
                events = api.get_xdxr_info(market, str(code))
            except Exception:
                fail += 1
                continue
            if not events:
                continue
            for e in events:
                # category 1 = 除权除息
                if int(e.get("category", 0)) != 1:
                    continue
                ex_date = int(f"{e['year']:04d}{e['month']:02d}{e['day']:02d}")
                records.append(
                    (
                        str(code).zfill(6),
                        ex_date,
                        float(e.get("songzhuangu") or 0.0),   # 送转股 (股/10股)
                        float(e.get("peigu") or 0.0),         # 配股 (股/10股)
                        float(e.get("peigujia") or 0.0),      # 配股价
                        float(e.get("fenhong") or 0.0),       # 分红 (元/10股)
                    )
                )
            ok += 1
            if verbose and i % 300 == 0:
                print(f"  ... {i}/{len(codes)}，已收集 {len(records)} 个事件", flush=True)
            if len(records) >= 5000:
                store.upsert_xdxr(records)
                records = []
    finally:
        try:
            api.disconnect()
        except Exception:
            pass

    if records:
        store.upsert_xdxr(records)
    if verbose:
        print(f"  完成：成功 {ok} 只，失败 {fail} 只")
    store.close()
    return ok


def apply_qfq(df: pd.DataFrame, xdxr: pd.DataFrame) -> pd.DataFrame:
    """把不复权日线转成前复权。

    Args:
        df: DatetimeIndex 索引，含 open/high/low/close/vol/amount。
        xdxr: 含 ex_date/bonus/rights/rights_px/dividend 的事件表。

    Returns:
        前复权后的 DataFrame 副本。amount 保持原值（成交额不受复权影响），
        vol 按因子反向缩放，保证 close*vol 仍近似等于 amount。
    """
    if df.empty:
        return df
    out = df.copy()
    out.attrs["adjust"] = "none"
    if xdxr is None or xdxr.empty:
        return out

    events = xdxr.copy()
    events["ex_date"] = events["ex_date"].astype(int)
    dates_int = out.index.strftime("%Y%m%d").astype(int)

    # 只保留落在数据区间内、且确实产生价格变动的事件
    lo, hi = int(dates_int[0]), int(dates_int[-1])
    events = events[(events["ex_date"] > lo) & (events["ex_date"] <= hi)]
    events = events[
        (events["bonus"] != 0)
        | (events["rights"] != 0)
        | (events["dividend"] != 0)
    ].sort_values("ex_date")
    if events.empty:
        return out

    factors = np.ones(len(out), dtype=float)
    close = out["close"].to_numpy(dtype=float)

    # 从最新事件往回推，累乘因子
    cumulative = 1.0
    for _, ev in events.iloc[::-1].iterrows():
        ex_date = int(ev["ex_date"])
        pos = int(np.searchsorted(dates_int, ex_date, side="left"))
        if pos <= 0 or pos >= len(out):
            continue
        prev_close = close[pos - 1]
        if prev_close <= 0:
            continue
        bonus = float(ev["bonus"]) / 10.0
        rights = float(ev["rights"]) / 10.0
        rights_px = float(ev["rights_px"])
        dividend = float(ev["dividend"]) / 10.0

        numerator = prev_close - dividend + rights * rights_px
        denominator = prev_close * (1.0 + bonus + rights)
        if denominator <= 0 or numerator <= 0:
            continue
        factor = numerator / denominator
        cumulative *= factor
        factors[:pos] *= factor

    for col in ("open", "high", "low", "close"):
        out[col] = out[col].to_numpy(dtype=float) * factors
    # 成交额不变，成交量反向调整，保持量价一致性
    out["vol"] = out["vol"].to_numpy(dtype=float) / np.where(factors == 0, 1.0, factors)
    out.attrs["adjust"] = "qfq"
    out.attrs["qfq_events"] = int(len(events))
    return out
