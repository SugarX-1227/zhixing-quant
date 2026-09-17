"""横截面打分：把因子原始值变成同一天内可比的分数，再合成总分。

为什么要做横截面标准化
----------------------

原始因子值量纲完全不同：`mom_60` 是 0.3 这种小数，`liquidity` 是 log(成交额)
≈ 20，`vol_ratio` 是 1.5。直接加权求和等于让 `liquidity` 单方面决定结果。

标准化必须在**单个交易日的横截面内**做，不能跨日期：

- 跨日期做 z-score 需要全样本的均值方差，那是未来信息，回测会虚高。
- 同一天内比较才符合实际决策——你要在今天命中的这 50 只里挑 5 只，
  比的就是「今天这批里谁更好」。

流程：原始值 → 去极值(MAD) → z-score → 乘方向 → 加权求和。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from zhixing_quant.factors.base import FACTOR_REGISTRY


def winsorize(s: pd.Series, n_mad: float = 5.0) -> pd.Series:
    """按中位数绝对偏差(MAD)去极值。

    比按分位数截断更抗少量极端值：A 股经常有一只票成交额是别人的 100 倍，
    用均值标准差会被它一个人拉走。

    Args:
        s: 单个横截面的因子值。
        n_mad: 保留中位数 ± n_mad × MAD 的范围。

    Returns:
        截断后的序列，NaN 原样保留。
    """
    x = pd.to_numeric(s, errors="coerce")
    if not x.notna().any():          # 全 NaN，pandas 的 median 会发空切片警告
        return x
    med = x.median()
    if not np.isfinite(med):
        return x
    mad = (x - med).abs().median()
    if not np.isfinite(mad) or mad == 0:
        return x
    lo, hi = med - n_mad * mad, med + n_mad * mad
    return x.clip(lo, hi)


def zscore(s: pd.Series) -> pd.Series:
    """横截面 z-score。全为同一个值时返回 0 而不是 NaN。"""
    x = pd.to_numeric(s, errors="coerce")
    if not x.notna().any():          # 全 NaN，直接返回，别让 pandas 去算空均值
        return x
    std = x.std()
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=x.index).where(x.notna())
    return (x - x.mean()) / std


def standardize(s: pd.Series, direction: int = 1, n_mad: float = 5.0) -> pd.Series:
    """去极值 + z-score + 乘方向，使「越大越好」。"""
    return zscore(winsorize(s, n_mad)) * (1 if direction >= 0 else -1)


def build_panel(
    data: Dict[str, pd.DataFrame],
    names: Sequence[str],
    dates: Optional[Iterable] = None,
) -> pd.DataFrame:
    """把每只标的的时间序列因子值拼成一张面板。

    Args:
        data: {code: 已算好指标的日线 DataFrame}，DatetimeIndex。
        names: 因子名列表。
        dates: 只保留这些日期，None 表示全部。

    Returns:
        MultiIndex (date, code) 的 DataFrame，每个因子一列。
        缺列导致算不出的因子整列为 NaN，**不抛异常**——但调用方应该用
        `coverage()` 检查，别让一个哑因子悄悄占着权重。
    """
    keep = set(pd.DatetimeIndex(dates)) if dates is not None else None
    frames: List[pd.DataFrame] = []
    for code, df in data.items():
        if df is None or df.empty:
            continue
        cols = {}
        for name in names:
            f = FACTOR_REGISTRY.get(name)
            if f is None:
                continue
            try:
                s = f.compute(df)
            except Exception:
                s = pd.Series(np.nan, index=df.index)
            cols[name] = pd.to_numeric(s, errors="coerce").reindex(df.index)
        if not cols:
            continue
        one = pd.DataFrame(cols, index=df.index)
        if keep is not None:
            one = one[one.index.isin(keep)]
        if one.empty:
            continue
        one["code"] = code
        frames.append(one.set_index("code", append=True))
    if not frames:
        return pd.DataFrame(columns=list(names))
    panel = pd.concat(frames).sort_index()
    panel.index.names = ["date", "code"]
    return panel


def coverage(panel: pd.DataFrame) -> pd.Series:
    """每个因子有多少比例的格子是非空的。

    低覆盖率 = 这个因子大部分时候算不出来（多半是缺指标列）。
    给它权重等于把权重丢掉，必须让使用者看见。
    """
    if panel.empty:
        return pd.Series(dtype=float)
    return panel.notna().mean().sort_values()


def score_panel(
    panel: pd.DataFrame,
    weights: Dict[str, float],
    n_mad: float = 5.0,
    min_names: int = 5,
) -> pd.Series:
    """逐日横截面标准化后加权求和，返回 (date, code) -> 总分。

    Args:
        panel: build_panel 的输出。
        weights: {因子名: 权重}。权重会被归一化到和为 1。
        n_mad: 去极值宽度。
        min_names: 横截面里少于这么多只标的时不做标准化（样本太少，
            z-score 没有意义），该日全部记 NaN。

    Returns:
        Series，索引与 panel 对齐。权重为空、或某日横截面太小、或该行一个
        因子都没算出来时为 NaN——**不是 0**。0 会被当成「中等水平」，
        实际含义是「没有分数」，两者必须能区分开。
    """
    used = {k: float(v) for k, v in (weights or {}).items()
            if k in panel.columns and float(v) != 0.0}
    if panel.empty or not used:
        return pd.Series(np.nan, index=panel.index, dtype=float)

    total_w = sum(abs(v) for v in used.values()) or 1.0
    out = pd.Series(0.0, index=panel.index)
    counted = pd.Series(0.0, index=panel.index)

    for date, chunk in panel.groupby(level="date"):
        if len(chunk) < min_names:
            continue
        for name, w in used.items():
            f = FACTOR_REGISTRY.get(name)
            direction = f.direction if f is not None else 1
            z = standardize(chunk[name], direction, n_mad)
            filled = z.fillna(0.0)
            out.loc[chunk.index] += filled * (w / total_w)
            # 记下这只标的在多少权重上真的有值，全缺的不该和别人同分
            counted.loc[chunk.index] += z.notna().astype(float) * (abs(w) / total_w)

    # 一个因子都没算出来的格子给 NaN，避免「全 0 分」被当成中等水平
    return out.where(counted > 0)


def rank_codes(
    data: Dict[str, pd.DataFrame],
    codes: Sequence[str],
    date,
    weights: Dict[str, float],
) -> pd.DataFrame:
    """给定日期，对一批候选按合成总分排序。扫描器用这个替代「按成交额降序」。

    Args:
        data: {code: 带指标的日线}。
        codes: 候选代码。
        date: 信号日（Timestamp 或可转换的值）。
        weights: 因子权重。

    Returns:
        DataFrame，列 code / score / 各因子的标准化分，按 score 降序。
        权重为空或标的太少时返回空表，调用方应退回原有排序。
    """
    if not weights or not codes:
        return pd.DataFrame(columns=["code", "score"])
    ts = pd.Timestamp(date)
    subset = {c: data[c] for c in codes if c in data}
    panel = build_panel(subset, list(weights), dates=[ts])
    if panel.empty:
        return pd.DataFrame(columns=["code", "score"])

    score = score_panel(panel, weights, min_names=1)
    chunk = panel.xs(ts, level="date", drop_level=True)
    out = pd.DataFrame(index=chunk.index)
    for name in weights:
        if name not in chunk.columns:
            continue
        f = FACTOR_REGISTRY.get(name)
        out[name] = standardize(chunk[name], f.direction if f else 1)
    out["score"] = score.xs(ts, level="date", drop_level=True)
    out = out.reset_index().rename(columns={"index": "code"})
    return out.sort_values("score", ascending=False, na_position="last").reset_index(
        drop=True)
