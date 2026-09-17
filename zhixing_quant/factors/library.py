"""内置因子库。

每个因子只用**当前行及之前**的数据，没有 shift(-n)，也不做跨日期的全样本
标准化——标准化统一放在 cross_section.py 的单个横截面里做。

选因子的原则：优先用这套系统已经算出来的东西（双线、砖型图、KDJ、量价），
而不是再引入一堆外部数据。A 股常见的市值 / 估值 / 财务因子这里一个都没有，
因为本地通达信库里没有这些字段——硬造代理指标只会引入噪声。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from zhixing_quant.factors.base import Factor, _col, _nan_like, register_factor
from zhixing_quant.indicators.tdx import atr as atr_of


# ---------------------------------------------------------------------------
# 动量 / 反转
# ---------------------------------------------------------------------------

def _ret(df: pd.DataFrame, n: int) -> pd.Series:
    close = _col(df, "close")
    if close is None:
        return _nan_like(df)
    return close / close.shift(n) - 1.0


register_factor(Factor(
    name="mom_20", label="20日动量", category="动量",
    compute=lambda df: _ret(df, 20), direction=1,
    help="近 20 个交易日涨幅。赌强者恒强。"))

register_factor(Factor(
    name="mom_60", label="60日动量", category="动量",
    compute=lambda df: _ret(df, 60), direction=1,
    help="近 60 个交易日涨幅。中期趋势强度。"))

register_factor(Factor(
    name="rev_5", label="5日反转", category="动量",
    compute=lambda df: _ret(df, 5), direction=-1,
    help="近 5 日涨幅，**越小越好**。短线超买的票次周往往回吐。"))


def _mom_ex_recent(df: pd.DataFrame) -> pd.Series:
    """剔除最近 5 日的 60 日动量：经典的「动量要中期、反转在短期」处理。"""
    close = _col(df, "close")
    if close is None:
        return _nan_like(df)
    return close.shift(5) / close.shift(60) - 1.0


register_factor(Factor(
    name="mom_60_ex5", label="60日动量(剔近5日)", category="动量",
    compute=_mom_ex_recent, direction=1,
    help="用 T-5 的价格算 60 日动量，避开短期反转对纯动量的污染。"))


# ---------------------------------------------------------------------------
# 趋势（知行双线）
# ---------------------------------------------------------------------------

def _dist_yellow(df: pd.DataFrame) -> pd.Series:
    close, yellow = _col(df, "close"), _col(df, "yellow_line")
    if close is None or yellow is None:
        return _nan_like(df)
    return (close / yellow.replace(0, np.nan) - 1.0).abs()


register_factor(Factor(
    name="dist_yellow", label="距黄线距离", category="趋势",
    compute=_dist_yellow, direction=-1, needs=("dual_line",),
    help="|收盘/黄线-1|，**越小越好**。贴着知行多空线的买点回撤空间最小。"))


def _line_spread(df: pd.DataFrame) -> pd.Series:
    white, yellow = _col(df, "white_line"), _col(df, "yellow_line")
    if white is None or yellow is None:
        return _nan_like(df)
    return white / yellow.replace(0, np.nan) - 1.0


register_factor(Factor(
    name="line_spread", label="白线黄线乖离", category="趋势",
    compute=_line_spread, direction=1, needs=("dual_line",),
    help="白线/黄线-1。正得越多越强势；但过大也意味着离黄线太远，追高风险。"))


def _yellow_slope(df: pd.DataFrame) -> pd.Series:
    yellow = _col(df, "yellow_line")
    if yellow is None:
        return _nan_like(df)
    return yellow / yellow.shift(10).replace(0, np.nan) - 1.0


register_factor(Factor(
    name="yellow_slope", label="黄线斜率(10日)", category="趋势",
    compute=_yellow_slope, direction=1, needs=("dual_line",),
    help="知行多空线自身的 10 日变化率。黄线拐头向上是结构转强的确认。"))


# ---------------------------------------------------------------------------
# 量能
# ---------------------------------------------------------------------------

def _vol_ratio(df: pd.DataFrame) -> pd.Series:
    vol = _col(df, "vol")
    if vol is None:
        return _nan_like(df)
    base = vol.shift(1).rolling(5, min_periods=2).mean()
    return vol / base.replace(0, np.nan)


register_factor(Factor(
    name="vol_ratio", label="量比(5日)", category="量能",
    compute=_vol_ratio, direction=1,
    help="当日量 / 前 5 日均量。放量是资金进场的直接证据。"))


def _amount_log(df: pd.DataFrame) -> pd.Series:
    amount = _col(df, "amount")
    if amount is None:
        return _nan_like(df)
    return np.log1p(amount.clip(lower=0))


register_factor(Factor(
    name="liquidity", label="成交额(对数)", category="量能",
    compute=_amount_log, direction=1,
    help="log(1+成交额)。不是选股逻辑，是流动性约束——买不进去的票分再高也没用。"))


def _turnover_stability(df: pd.DataFrame) -> pd.Series:
    """成交额的稳定度：20 日变异系数，越小说明资金关注度越持续。"""
    amount = _col(df, "amount")
    if amount is None:
        return _nan_like(df)
    mean = amount.rolling(20, min_periods=10).mean()
    std = amount.rolling(20, min_periods=10).std()
    return std / mean.replace(0, np.nan)


register_factor(Factor(
    name="amount_cv", label="成交额变异系数", category="量能",
    compute=_turnover_stability, direction=-1,
    help="20 日成交额标准差/均值，**越小越好**。忽大忽小多半是游资一日游。"))


# ---------------------------------------------------------------------------
# 波动
# ---------------------------------------------------------------------------

def _atr_pct(df: pd.DataFrame) -> pd.Series:
    close = _col(df, "close")
    if close is None or "high" not in df.columns or "low" not in df.columns:
        return _nan_like(df)
    a = atr_of(pd.to_numeric(df["high"], errors="coerce"),
               pd.to_numeric(df["low"], errors="coerce"), close, 14)
    return a / close.replace(0, np.nan)


register_factor(Factor(
    name="atr_pct", label="ATR占价比", category="波动",
    compute=_atr_pct, direction=-1,
    help="ATR(14)/收盘，**越小越好**。同样的止损距离，低波动票能买更多股。"))


def _downside_vol(df: pd.DataFrame) -> pd.Series:
    close = _col(df, "close")
    if close is None:
        return _nan_like(df)
    ret = close.pct_change(fill_method=None)
    return ret.clip(upper=0).rolling(20, min_periods=10).std()


register_factor(Factor(
    name="downside_vol", label="下行波动(20日)", category="波动",
    compute=_downside_vol, direction=-1,
    help="只统计下跌日的波动，**越小越好**。比总波动更贴近「回撤风险」。"))


# ---------------------------------------------------------------------------
# 位置
# ---------------------------------------------------------------------------

def _dist_high(df: pd.DataFrame) -> pd.Series:
    close, high = _col(df, "close"), _col(df, "high")
    if close is None or high is None:
        return _nan_like(df)
    hh = high.rolling(60, min_periods=20).max()
    return close / hh.replace(0, np.nan) - 1.0


register_factor(Factor(
    name="dist_high_60", label="距60日高点", category="位置",
    compute=_dist_high, direction=1,
    help="收盘/60日最高-1（负值）。越接近 0 越靠近新高，配合动量用。"))


def _drawdown_60(df: pd.DataFrame) -> pd.Series:
    close = _col(df, "close")
    if close is None:
        return _nan_like(df)
    roll_max = close.rolling(60, min_periods=20).max()
    dd = close / roll_max.replace(0, np.nan) - 1.0
    return dd.rolling(60, min_periods=20).min().abs()


register_factor(Factor(
    name="maxdd_60", label="60日最大回撤", category="位置",
    compute=_drawdown_60, direction=-1,
    help="近 60 日最大回撤幅度，**越小越好**。走得稳的票持有体验和止损都更好。"))


# ---------------------------------------------------------------------------
# 形态（复用战法指标）
# ---------------------------------------------------------------------------

def _brick_strength(df: pd.DataFrame) -> pd.Series:
    brick = _col(df, "brick_value")
    if brick is None:
        return _nan_like(df)
    prev = brick.shift(1)
    return brick - prev


register_factor(Factor(
    name="brick_red_height", label="砖型图红柱高度", category="形态",
    compute=_brick_strength, direction=1, needs=("brick",),
    help="今日砖高 - 昨日砖高。红柱越高，当日多方推动越强。"))


def _kdj_j(df: pd.DataFrame) -> pd.Series:
    j = _col(df, "kdj_j")
    return j if j is not None else _nan_like(df)


register_factor(Factor(
    name="kdj_j", label="KDJ J值", category="形态",
    compute=_kdj_j, direction=-1, needs=("b1",),
    help="J 值，**越小越好**。同为 B1 信号时，J 更低的超卖更充分。"))


# ---------------------------------------------------------------------------
# 预设组合
# ---------------------------------------------------------------------------
#
# 权重是**初始猜测值**，必须用 evaluate.py 的 IC / 分层收益校准。
# 界面上会标出来，不要当成结论。

PRESETS = {
    "trend": {
        "label": "趋势跟随",
        "weights": {"mom_60_ex5": 1.0, "yellow_slope": 0.8, "line_spread": 0.5,
                    "atr_pct": 0.4, "liquidity": 0.3},
        "help": "偏好中期动量强、黄线上行、波动可控的票。",
    },
    "pullback": {
        "label": "回踩买点",
        "weights": {"dist_yellow": 1.0, "mom_60_ex5": 0.6, "rev_5": 0.5,
                    "downside_vol": 0.4, "liquidity": 0.3},
        "help": "偏好贴近黄线、中期仍强、短线刚回调的票。配合 B1 用。",
    },
    "breakout": {
        "label": "放量突破",
        "weights": {"vol_ratio": 1.0, "dist_high_60": 0.8, "mom_20": 0.6,
                    "amount_cv": 0.3, "liquidity": 0.3},
        "help": "偏好放量、逼近新高的票。配合 B2 / 砖型图用。",
    },
    "liquidity_only": {
        "label": "仅成交额（旧口径）",
        "weights": {"liquidity": 1.0},
        "help": "等价于改造前「按成交额降序」的排序，留作对照基准。",
    },
}


def preset_weights(name: str) -> dict:
    """取预设组合的权重。未知名字返回空字典（调用方退化为不排序）。"""
    return dict(PRESETS.get(name, {}).get("weights", {}))
