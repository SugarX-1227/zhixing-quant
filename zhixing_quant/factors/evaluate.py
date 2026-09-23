"""因子有效性检验：IC / ICIR / 分层收益。

回答的问题是「这个因子到底有没有用」，而不是「用它回测能赚多少」。
回测把因子、战法、出场规则、仓位、成本全搅在一起，一个数字说不清是谁的功劳。
IC 只看一件事：**今天的因子排序，能不能预测未来 N 天的收益排序。**

⚠️ 本模块是唯一允许读未来数据的地方。`forward_returns` 按定义就要看
T → T+N 的收益，那是被解释变量。任何把它的输出接进信号或排序的写法都是
彻头彻尾的未来函数——所以这里的函数名全部带 forward / ic 前缀，一眼能认出来。

口径
----

IC     某一天，全市场因子值与未来 N 日收益的**秩相关**（Spearman）。
       用秩相关而不是皮尔逊，是因为因子值分布往往有厚尾，
       少数极端值会主导皮尔逊相关。
ICIR   IC 序列的 均值 / 标准差。衡量的是「稳不稳」，比单看均值重要得多：
       IC 均值 0.05 但每天正负横跳，和稳定 0.03，后者能用。
分层   按因子把每天的标的分成 Q 组，看各组未来收益的均值。
       单调性比头尾差更能说明问题——只有第一组好、中间乱，多半是噪声。

经验参考（A 股日频）：|IC| > 0.03 算有信号，ICIR > 0.3 算稳定。
样本不足 60 个交易日的结论不要当真。
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

from zhixing_quant.factors.base import FACTOR_REGISTRY


def forward_returns(data: Dict[str, pd.DataFrame], horizon: int = 5,
                    price_col: str = "close") -> pd.Series:
    """未来 N 个交易日的收益率，索引 (date, code)。

    ⚠️ 这是被解释变量，**只能用于研究**。接进选股或回测信号就是未来函数。

    口径与回测一致：T 日收盘出信号、T+1 开盘买入的话，真正吃到的是
    T+1 → T+1+N 的收益。这里仍按 T → T+N 的收盘计算，因为 IC 衡量的是
    因子的**排序能力**，平移一天不改变相对排序的结论，而且和业界口径一致。

    Args:
        data: {code: 日线 DataFrame}。
        horizon: 未来多少个交易日。
        price_col: 用哪一列算收益。

    Returns:
        Series，索引 (date, code)。最后 horizon 根 K 线为 NaN。
    """
    frames = []
    for code, df in data.items():
        if df is None or df.empty or price_col not in df.columns:
            continue
        px = pd.to_numeric(df[price_col], errors="coerce")
        fwd = px.shift(-horizon) / px - 1.0        # 唯一合法的 shift(-n)
        one = pd.DataFrame({"fwd": fwd}, index=df.index)
        one["code"] = code
        frames.append(one.set_index("code", append=True))
    if not frames:
        return pd.Series(dtype=float)
    out = pd.concat(frames).sort_index()["fwd"]
    out.index.names = ["date", "code"]
    return out


def _corr(a: pd.Series, b: pd.Series, method: str = "spearman") -> float:
    """相关系数。

    pandas 的 `Series.corr(method="spearman")` 要 scipy，而本项目刻意保持
    零重依赖（requirements 只有 pandas/numpy/yaml/streamlit/plotly）。
    秩相关本来就是「先取秩再算皮尔逊」，自己排一次秩即可，结果完全一致。
    """
    if method == "spearman":
        a, b = a.rank(), b.rank()
    return float(a.corr(b))


def factor_ic(panel: pd.DataFrame, fwd: pd.Series,
              method: str = "spearman", min_names: int = 10) -> pd.DataFrame:
    """逐日算每个因子与未来收益的相关系数。

    Args:
        panel: cross_section.build_panel 的输出。
        fwd: forward_returns 的输出。
        method: "spearman"（默认，秩相关）或 "pearson"。
        min_names: 横截面里少于这么多只有效标的就跳过该日。

    Returns:
        DataFrame，索引为日期，每个因子一列，值为当日 IC。
    """
    if panel.empty or fwd.empty:
        return pd.DataFrame()
    joined = panel.join(fwd.rename("__fwd"), how="inner")
    rows = {}
    for date, chunk in joined.groupby(level="date"):
        y = chunk["__fwd"]
        vals = {}
        for name in panel.columns:
            pair = pd.concat([chunk[name], y], axis=1).dropna()
            if len(pair) < min_names:
                continue
            f = FACTOR_REGISTRY.get(name)
            direction = f.direction if f is not None else 1
            c = _corr(pair.iloc[:, 0], pair.iloc[:, 1], method)
            if np.isfinite(c):
                # 乘方向，让「越大越好」的因子 IC 为正，便于横向比较
                vals[name] = c * (1 if direction >= 0 else -1)
        if vals:
            rows[date] = vals
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).T.sort_index()


def ic_summary(ic: pd.DataFrame) -> pd.DataFrame:
    """把逐日 IC 汇总成一张可读的表。

    Returns:
        每个因子一行：IC均值 / IC标准差 / ICIR / t值 / 正IC占比 / 有效天数。
        按 |ICIR| 降序——稳定性比幅度更值得先看。
    """
    if ic is None or ic.empty:
        return pd.DataFrame(columns=["因子", "IC均值", "IC标准差", "ICIR", "t值",
                                     "方向", "正IC占比", "有效天数"])
    rows = []
    for name in ic.columns:
        s = ic[name].dropna()
        n = len(s)
        if n == 0:
            continue
        mean, std = float(s.mean()), float(s.std())
        icir = mean / std if std > 0 else 0.0
        t = icir * np.sqrt(n)
        rows.append({
            "因子": name,
            "IC均值": round(mean, 4),
            "IC标准差": round(std, 4),
            "ICIR": round(icir, 3),
            # IC 序列近似独立，t = ICIR × sqrt(n)
            "t值": round(t, 2),
            "方向": direction_verdict(t),
            "正IC占比": round(float((s > 0).mean()), 3),
            "有效天数": n,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.reindex(out["ICIR"].abs().sort_values(ascending=False).index
                       ).reset_index(drop=True)


T_SIGNIFICANT = 2.0          # |t| 达到这个值才认为方向是真的，不是噪声


def direction_verdict(t: float) -> str:
    """因子声明的方向和实测方向对不对得上。

    IC 已经按 `Factor.direction` 调过符号，所以：
        t > +2   实测与声明一致，可以用
        t < -2   **实测与声明相反**——这个因子在这段样本里是反着的，
                 照声明的方向给它正权重，等于系统性地挑最差的标的
        其余     不显著，给它权重等于把权重丢掉

    这一列是整张表里最该先看的。本项目的预设权重是拍脑袋给的初始值，
    实测下来有的组合 3/5 个因子方向都是反的。
    """
    if not np.isfinite(t):
        return "不显著"
    if t >= T_SIGNIFICANT:
        return "一致"
    if t <= -T_SIGNIFICANT:
        return "⚠ 相反"
    return "不显著"


def weights_from_ic(summary: pd.DataFrame, max_factors: int = 6,
                    min_abs_t: float = T_SIGNIFICANT,
                    allow_flip: bool = True) -> Dict[str, float]:
    """直接从实测 IC 生成因子权重，替代拍脑袋配权重。

    规则很简单，刻意不做复杂优化（因子少、样本短的时候，复杂加权
    只会把噪声也一起拟合进去）：

    1. 只保留 |t| >= min_abs_t 的因子，其余一律丢掉；
    2. 权重 ∝ ICIR 的绝对值，归一化到最大权重为 1；
    3. 实测方向与声明相反的因子（ICIR < 0），权重取负——等价于反着用它。
       `allow_flip=False` 则直接剔除这类因子。

    Args:
        summary: ic_summary 的输出。
        max_factors: 最多留几个。留太多等于在短样本上过拟合。
        min_abs_t: 显著性门槛。
        allow_flip: 是否允许把方向相反的因子反过来用。

    Returns:
        {因子名: 权重}，可直接写进 settings.yaml 的 factors.weights。
        没有任何因子达标时返回空字典——那说明这批因子在这段样本里
        都没用，**应该退回不排序，而不是硬凑一个组合出来**。
    """
    if summary is None or summary.empty or "t值" not in summary.columns:
        return {}
    df = summary[summary["t值"].abs() >= float(min_abs_t)].copy()
    if not allow_flip:
        df = df[df["t值"] > 0]
    if df.empty:
        return {}
    df = df.reindex(df["ICIR"].abs().sort_values(ascending=False).index)
    df = df.head(int(max_factors))
    peak = df["ICIR"].abs().max()
    if not np.isfinite(peak) or peak == 0:
        return {}
    return {str(r["因子"]): round(float(r["ICIR"]) / peak, 3)
            for _, r in df.iterrows()}


def stable_weights(is_summary: pd.DataFrame, oos_summary: pd.DataFrame,
                   max_factors: int = 6, min_abs_t: float = T_SIGNIFICANT
                   ) -> Dict[str, float]:
    """只留**样本内外都显著、且方向一致**的因子，权重按两段 ICIR 的几何均值。

    为什么不能只看全样本：全样本把样本内外混在一起算，一个只在样本内有效
    的因子照样能排进前列。2026-09 实测就撞上了——`atr_pct` 样本内
    ICIR +0.301（t=7.61），样本外掉到 +0.032（t=0.59），完全失效，
    但全样本 ICIR 仍有 +0.200，自动配权重时被排到第二位。

    要求两段同时显著且同号，等于让因子自己先过一次样本外考试。
    权重用几何均值（保留符号）而不是算术平均：一段强一段弱的因子会被
    压下去，两段都稳的才能拿高权重。

    Args:
        is_summary / oos_summary: 两段各自的 ic_summary 输出。
        max_factors: 最多留几个。
        min_abs_t: 两段都要达到的显著性门槛。

    Returns:
        {因子名: 权重}，最大权重归一化到 1；方向相反的取负权重。
        没有因子两段都过关时返回空——那说明这批因子没有稳定的方向，
        应该退回不排序。
    """
    if (is_summary is None or oos_summary is None
            or is_summary.empty or oos_summary.empty):
        return {}
    a = is_summary.set_index("因子")
    b = oos_summary.set_index("因子")
    rows = []
    for name in a.index.intersection(b.index):
        ta, tb = float(a.loc[name, "t值"]), float(b.loc[name, "t值"])
        ia, ib = float(a.loc[name, "ICIR"]), float(b.loc[name, "ICIR"])
        if abs(ta) < min_abs_t or abs(tb) < min_abs_t:
            continue
        if np.sign(ia) != np.sign(ib):       # 两段方向相反 = 没有稳定方向
            continue
        rows.append((name, np.sign(ia) * float(np.sqrt(abs(ia) * abs(ib)))))
    if not rows:
        return {}
    rows.sort(key=lambda kv: -abs(kv[1]))
    rows = rows[:int(max_factors)]
    peak = max(abs(v) for _, v in rows)
    return {k: round(v / peak, 3) for k, v in rows}


def stability_report(is_summary: pd.DataFrame,
                     oos_summary: pd.DataFrame) -> pd.DataFrame:
    """每个因子在样本内外的表现对照，直接看出哪些是样本内幻觉。"""
    if is_summary is None or oos_summary is None:
        return pd.DataFrame()
    a = is_summary.set_index("因子")
    b = oos_summary.set_index("因子")
    rows = []
    for name in a.index.union(b.index):
        ia = float(a.loc[name, "ICIR"]) if name in a.index else float("nan")
        ib = float(b.loc[name, "ICIR"]) if name in b.index else float("nan")
        ta = float(a.loc[name, "t值"]) if name in a.index else 0.0
        tb = float(b.loc[name, "t值"]) if name in b.index else 0.0
        ok_a, ok_b = abs(ta) >= T_SIGNIFICANT, abs(tb) >= T_SIGNIFICANT
        if ok_a and ok_b and np.sign(ia) == np.sign(ib):
            verdict = "稳定" + ("（反向用）" if ia < 0 else "")
        elif ok_a and not ok_b:
            verdict = "⚠ 样本内幻觉"
        elif ok_b and not ok_a:
            verdict = "只在样本外显著，存疑"
        elif ok_a and ok_b:
            verdict = "⚠ 两段方向相反"
        else:
            verdict = "始终不显著"
        rows.append({"因子": name, "样本内ICIR": round(ia, 3),
                     "样本外ICIR": round(ib, 3), "判定": verdict})
    out = pd.DataFrame(rows)
    order = {"稳定": 0, "稳定（反向用）": 0, "⚠ 样本内幻觉": 1,
             "只在样本外显著，存疑": 2, "⚠ 两段方向相反": 3, "始终不显著": 4}
    return out.sort_values(
        ["判定", "样本外ICIR"],
        key=lambda c: c.map(order) if c.name == "判定" else -c.abs()
    ).reset_index(drop=True)


def weights_as_yaml(weights: Dict[str, float], strategy: str = "b2") -> str:
    """把权重渲染成可以直接粘进 settings.yaml 的片段。"""
    if not weights:
        return ("# 没有因子达到显著性门槛。这批因子在这段样本里都没用，\n"
                "# 应该退回不排序：factors.by_strategy.%s: amount" % strategy)
    lines = ["factors:", "  by_strategy:", f"    {strategy}: custom",
             "  weights:"]
    for k, v in sorted(weights.items(), key=lambda kv: -abs(kv[1])):
        note = "  # 实测方向与声明相反，这里反着用" if v < 0 else ""
        lines.append(f"    {k}: {v}{note}")
    return "\n".join(lines)


def quantile_returns(panel: pd.DataFrame, fwd: pd.Series, factor: str,
                     q: int = 5, min_names: int = 10) -> pd.DataFrame:
    """按因子分 Q 层，看各层未来收益的均值。

    单调性是关键：Q1 < Q2 < ... < Q5 才说明因子在整个取值范围上都有效。
    只有两头有差异、中间乱成一团，多半是几个极端值造成的假象。

    Args:
        panel: 因子面板。
        fwd: 未来收益。
        factor: 要分层的因子名。
        q: 分几层。
        min_names: 横截面样本下限。

    Returns:
        DataFrame，索引 Q1..Qq，列为 平均未来收益 / 胜率 / 样本数。
    """
    empty = pd.DataFrame(columns=["平均未来收益", "胜率", "样本数"])
    if panel.empty or fwd.empty or factor not in panel.columns:
        return empty
    f = FACTOR_REGISTRY.get(factor)
    direction = f.direction if f is not None else 1

    joined = panel[[factor]].join(fwd.rename("__fwd"), how="inner").dropna()
    if joined.empty:
        return empty

    buckets = []
    for _date, chunk in joined.groupby(level="date"):
        if len(chunk) < min_names:
            continue
        vals = chunk[factor] * (1 if direction >= 0 else -1)
        try:
            lab = pd.qcut(vals.rank(method="first"), q,
                          labels=[f"Q{i}" for i in range(1, q + 1)])
        except ValueError:
            continue           # 该日取值全同，分不了层
        buckets.append(pd.DataFrame({"bucket": lab, "fwd": chunk["__fwd"]}))
    if not buckets:
        return empty

    allb = pd.concat(buckets)
    g = allb.groupby("bucket", observed=True)["fwd"]
    out = pd.DataFrame({
        "平均未来收益": g.mean().round(5),
        "胜率": g.apply(lambda s: float((s > 0).mean())).round(3),
        "样本数": g.size(),
    })
    return out.reindex([f"Q{i}" for i in range(1, q + 1)]).dropna(how="all")


def monotonicity(qret: pd.DataFrame) -> float:
    """分层收益的单调性：相邻层递增的比例，1.0 = 完全单调。

    低于 0.6 基本可以认为这个因子没有稳定的方向性。
    """
    if qret is None or qret.empty or "平均未来收益" not in qret.columns:
        return 0.0
    v = qret["平均未来收益"].dropna().to_numpy()
    if len(v) < 2:
        return 0.0
    return float((np.diff(v) > 0).mean())


def strategy_population(data: Dict[str, pd.DataFrame], sig_col: str,
                        regime: Optional[pd.Series] = None) -> pd.Series:
    """战法命中人群：当日 `sig_col` 为 True 的 (date, code)。

    排序只作用于命中信号的那十几只，因子在这个人群里的规律和全市场不同——
    2026-09 全量实测 B2：`amount_cv` 全市场 ICIR +0.54、命中人群里 +0.01；
    `vol_ratio` 全市场 -0.18、命中人群里 +0.20。拿全市场 IC 给战法配权重，
    样本外 -23.2%，手写预设 +9.7%。

    Args:
        data: {code: 已算好战法指标的日线}，须含 sig_col。
        sig_col: 信号列名，如 "sig_b2"。
        regime: 可选，按收盘日索引的区间序列（值为 BULL/BEAR/NEUTRAL），
            见 timing.active_value.regime_by_close。给了就只留 BULL——
            空头区间引擎禁止开仓，那天的命中根本不会被排序。

    Returns:
        布尔 Series，索引 (date, code)，只含 True 的行。
    """
    parts = []
    for code, df in data.items():
        if df is None or df.empty or sig_col not in df.columns:
            continue
        s = df[sig_col].fillna(False).astype(bool)
        if regime is not None:
            s = s & (regime.reindex(df.index).to_numpy() == "BULL")
        s = s[s]
        if s.empty:
            continue
        parts.append(pd.Series(True, index=pd.MultiIndex.from_arrays(
            [s.index, [code] * len(s)], names=["date", "code"])))
    if not parts:
        return pd.Series(dtype=bool, index=pd.MultiIndex.from_arrays(
            [[], []], names=["date", "code"]))
    return pd.concat(parts).sort_index()


def evaluate_factors(
    data: Dict[str, pd.DataFrame],
    names: Sequence[str],
    horizon: int = 5,
    q: int = 5,
    start: Optional[str] = None,
    end: Optional[str] = None,
    population: Optional[pd.Series] = None,
) -> dict:
    """一次算完 IC 表和每个因子的分层收益。界面和脚本都用这个入口。

    Args:
        data: {code: 已算好指标的日线}。
        names: 要检验的因子。
        horizon: 未来收益天数。
        q: 分层数。
        start / end: YYYYMMDD，限定评估区间。
        population: 可选，只在这些 (date, code) 上算 IC 和分层，
            通常来自 `strategy_population`。筛选发生在因子算完之后，
            不会因为筛人群额外丢样本。

    Returns:
        {"ic": 逐日IC, "summary": 汇总表, "quantiles": {因子: 分层表},
         "coverage": 各因子覆盖率, "warnings": [...]}
    """
    from zhixing_quant.factors.cross_section import build_panel, coverage

    warnings = []
    sliced = {}
    lo = pd.Timestamp(start) if start else None
    hi = pd.Timestamp(end) if end else None
    for code, df in data.items():
        if df is None or df.empty:
            continue
        d = df
        if lo is not None:
            d = d[d.index >= lo]
        if hi is not None:
            d = d[d.index <= hi]
        if len(d) > horizon:
            sliced[code] = d
    if not sliced:
        return {"ic": pd.DataFrame(), "summary": pd.DataFrame(), "quantiles": {},
                "coverage": pd.Series(dtype=float),
                "warnings": ["区间内没有可用数据。"]}

    panel = build_panel(sliced, list(names))
    if population is not None:
        keep = population.reindex(panel.index, fill_value=False).to_numpy(dtype=bool)
        panel = panel[keep]
        per_day = panel.groupby(level="date").size()
        days = len(per_day)
        warnings.append(
            f"只在指定人群上评估：{len(panel):,} 个样本、{days} 个交易日，"
            f"每天中位 {per_day.median() if days else 0:.0f} 只，"
            f"其中 {int((per_day < 10).sum())} 天不足 10 只、不计 IC。")
        if panel.empty:
            return {"ic": pd.DataFrame(), "summary": pd.DataFrame(), "quantiles": {},
                    "coverage": pd.Series(dtype=float), "warnings": warnings}
    cov = coverage(panel)
    dead = [n for n, v in cov.items() if v < 0.2]
    if dead:
        warnings.append(
            f"以下因子覆盖率低于 20%，多半是缺指标列算不出来，"
            f"给它们权重等于把权重丢掉：{'、'.join(dead)}"
        )

    fwd = forward_returns(sliced, horizon=horizon)
    ic = factor_ic(panel, fwd)
    summary = ic_summary(ic)
    if not ic.empty and len(ic) < 60:
        warnings.append(
            f"只有 {len(ic)} 个交易日的 IC 样本，统计上说明不了问题。"
            "至少拉到 60 个交易日再看结论。"
        )

    quantiles = {n: quantile_returns(panel, fwd, n, q=q) for n in panel.columns}
    return {"ic": ic, "summary": summary, "quantiles": quantiles,
            "coverage": cov, "warnings": warnings}
