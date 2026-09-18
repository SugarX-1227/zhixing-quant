"""MACD indicator with zero-axis, divergence, and false-cross detection."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import cross


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    divergence_window: int = 60,
) -> pd.DataFrame:
    """Append MACD columns.

    Args:
        df: Daily OHLCV DataFrame.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal line period.
        divergence_window: Lookback for divergence detection.

    Returns:
        Copy of df with MACD columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 3.5 节:
        - 固定参数 (12, 26, 9)
        - 三大用法：零轴多空、顶/底背离、金叉空/死叉多
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)

    out["dif"] = dif
    out["dea"] = dea
    out["macd_hist"] = macd_hist
    out["above_zero"] = dif > 0

    # 背离。原实现是逐根 K 线切片 + idxmin 的 Python 循环，单只 1200 根
    # K 线要 246ms，占整条防守流水线 90% 的时间——消融和参数校准动辄跑
    # 几十次回测，这一处就是主瓶颈。改成滚动窗口的向量化写法。
    #
    # ⚠️ 同时修掉一个判据错误：**顶背离原本拿窗口最低点当「前高」比**。
    #
    #     p_prev_low_idx = win_p.iloc[:-1].idxmin()      # 最低点
    #     if cur_p > prev_p and cur_d < prev_d:          # 却用于顶背离
    #
    # 「今天收盘高于过去 60 天最低点」几乎恒为真，所以那个条件实际退化成
    # 「DIF 跌破过去 60 天最低」——和顶背离（价格创新高、DIF 不创新高）
    # 完全是两回事。而 signals/macd_veto.py 正拿它否决买入信号，
    # 等于按一个错误的条件在拦单。顶背离现在按定义比**前高**。
    #
    # 窗口口径与原实现一致：比较对象是 [i-W, i-1] 这 W 根，不含当日。
    prev_low_p = close.shift(1).rolling(divergence_window).min()
    prev_low_d = dif.shift(1).rolling(divergence_window).min()
    prev_high_p = close.shift(1).rolling(divergence_window).max()
    prev_high_d = dif.shift(1).rolling(divergence_window).max()

    # 底背离：价格创新低，DIF 不创新低
    bull_div = (close < prev_low_p) & (dif > prev_low_d)
    # 顶背离：价格创新高，DIF 不创新高
    bear_div = (close > prev_high_p) & (dif < prev_high_d)
    bull_div = bull_div.fillna(False)
    bear_div = bear_div.fillna(False)

    out["bull_divergence"] = bull_div
    out["bear_divergence"] = bear_div

    # False golden/death cross
    gold = cross(dif, dea)
    death = cross(dea, dif)
    out["false_golden_cross"] = gold & (dif.diff() < 0)
    out["false_death_cross"] = death & (dif.diff() > 0)

    return out
