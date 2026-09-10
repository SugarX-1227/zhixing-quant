"""TongDaXin-compatible indicator helpers."""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd


def ref(series: pd.Series, n: int = 1) -> pd.Series:
    """TongDaXin REF(X, N): previous N bars."""
    return series.shift(n)


def hhv(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin HHV(X, N): rolling highest value."""
    return series.rolling(n, min_periods=1).max()


def llv(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin LLV(X, N): rolling lowest value."""
    return series.rolling(n, min_periods=1).min()


def sma_tdx(series: pd.Series, n: int, m: int) -> pd.Series:
    """TongDaXin SMA(X, N, M).

    Args:
        series: Input numeric series.
        n: TDX smoothing period.
        m: TDX weight.

    Returns:
        Smoothed series where Y[t] = (M*X[t] + (N-M)*Y[t-1]) / N.
    """
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    prev = np.nan
    for i, value in enumerate(values):
        if np.isnan(value):
            out[i] = prev
            continue
        if np.isnan(prev):
            prev = value
        else:
            prev = (m * value + (n - m) * prev) / n
        out[i] = prev
    return pd.Series(out, index=series.index)


def ma(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin MA(X, N): simple moving average."""
    return series.rolling(n, min_periods=1).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """TongDaXin EMA(X, N): exponential moving average."""
    return pd.to_numeric(series, errors="coerce").ewm(span=span, adjust=False).mean()


def average_ma(series: pd.Series, windows: Iterable[int]) -> pd.Series:
    """Average several TDX MA lines into one line."""
    lines = [ma(series, int(window)) for window in windows]
    return sum(lines) / len(lines)


# ---------------------------------------------------------------------------
# 知行双线：全项目唯一定义
# ---------------------------------------------------------------------------
#
# 战法文档附录 B.1（通达信原始源码）：
#
#     知行短期趋势线（白线）: EMA(EMA(C,10),10)
#     知行多空线    （黄线）: (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4
#
# 白线是**双重** EMA10，不是单条 EMA10，更不是 MA10。
# 黄线是四条均线的平均，不是 MA20。含 MA114 意味着它是中长期锚——
# 这正是"跌破黄线 = 清仓 + 移出股票池"这种重处置的依据。用 MA20 代替，
# 实测会让破线次数多出四成，把大量正常回调判成结构破坏。
#
# 历史教训：dual_line.py 曾用 ema(C,10) / ma(C,20)，而 b1/b2/brick 用的是
# 正确公式，四个模块都往 yellow_line 这一列写值，流水线里后写覆盖先写。
# 结果是进攻端和防守端跑在两条不同的线上，且列存在、值是错的，
# 覆盖检查看不出来。所以现在收敛到这里，任何模块都不许自己再算一遍。

ZHIXING_WHITE_SPAN = 10
ZHIXING_YELLOW_WINDOWS = (14, 28, 57, 114)


def zhixing_white(series: pd.Series, span: int = ZHIXING_WHITE_SPAN) -> pd.Series:
    """知行短期趋势线（白线）= EMA(EMA(C,10),10)。"""
    close = pd.to_numeric(series, errors="coerce")
    return ema(ema(close, span), span)


def zhixing_yellow(series: pd.Series,
                   windows: Iterable[int] = ZHIXING_YELLOW_WINDOWS) -> pd.Series:
    """知行多空线（黄线）= (MA14+MA28+MA57+MA114)/4。"""
    close = pd.to_numeric(series, errors="coerce")
    return average_ma(close, windows)


def attach_zhixing_lines(out: pd.DataFrame, cfg: Optional[dict] = None) -> pd.DataFrame:
    """把双线及其派生列写进 out（原地），并返回 out。

    任何需要白线/黄线的指标模块都应该调这个，而不是自己算。因为所有模块
    算出来的值完全相同，流水线里谁先谁后跑都无所谓——覆盖变成幂等操作。

    Args:
        out: 含 close 列的 DataFrame。
        cfg: 可选配置，读 cfg["dual_line"]["white_span"] / ["yellow_ma_windows"]。
            缺省用文档定义的 10 与 (14,28,57,114)。

    Returns:
        同一个 out 对象。

    Rule source:
        Z哥战法-完整战法详解.md 附录 B.1
    """
    c = (cfg or {}).get("dual_line", {}) or {}
    span = int(c.get("white_span", ZHIXING_WHITE_SPAN))
    windows = tuple(c.get("yellow_ma_windows", ZHIXING_YELLOW_WINDOWS))

    close = pd.to_numeric(out["close"], errors="coerce")
    white = zhixing_white(close, span)
    yellow = zhixing_yellow(close, windows)

    # 预热保护。ma() 用的是 min_periods=1，K 线不足时不会返回 NaN，而是拿
    # 残缺窗口算出一个看起来很正常的值。含 MA114 的黄线在第 30 根 K 线上
    # 算出来的东西没有任何意义，文档 3.1 明确提醒过次新股/刚复牌的票黄线失真。
    # 这里直接置 NaN，并单独给一列 lines_valid，让调用方能区分
    # "没触发" 和 "历史不够、这条规则根本没法判"。
    warmup = max(int(w) for w in windows)
    valid = pd.Series(range(1, len(out) + 1), index=out.index) >= warmup
    yellow = yellow.where(valid)

    out["white_line"] = white
    out["yellow_line"] = yellow
    out["lines_valid"] = valid
    out["above_white"] = close > white
    out["above_yellow"] = close > yellow
    out["regime_strong"] = white > yellow      # 强势多头，所有买点的前提
    out["regime_weak"] = white < yellow        # 纯空头，任何反弹都无参与价值
    out["golden_cross"] = cross(white, yellow)
    out["death_cross"] = cross(yellow, white)
    return out


def exist(series: pd.Series, n: int) -> pd.Series:
    """TongDaXin EXIST(X, N): True if X was True within the last N bars."""
    return series.rolling(n, min_periods=1).max().astype(bool)


def cross(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """TongDaXin CROSS(A, B): A crosses above B on the current bar."""
    a = pd.to_numeric(series_a, errors="coerce")
    b = pd.to_numeric(series_b, errors="coerce")
    return (a > b) & (a.shift(1) <= b.shift(1))


def effective_color(open_: pd.Series, close: pd.Series, pre_close: pd.Series) -> pd.Series:
    """Fake-yang-real-yin rule: close > open but close < pre_close counts as bear."""
    up = close > open_
    fake_yang = (close >= open_) & (close < pre_close)
    return pd.Series(np.where(fake_yang, "bear", np.where(up, "bull", "bear")), index=close.index)

