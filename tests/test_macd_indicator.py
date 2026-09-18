import pandas as pd

from zhixing_quant.indicators.macd import add_macd


def test_macd_basic_columns():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100 + i for i in range(30)],
            "high": [102 + i for i in range(30)],
            "low": [98 + i for i in range(30)],
            "close": [101 + i for i in range(30)],
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_macd(df)

    assert "dif" in result.columns
    assert "dea" in result.columns
    assert "macd_hist" in result.columns
    assert "above_zero" in result.columns
    assert "bull_divergence" in result.columns
    assert "bear_divergence" in result.columns
    assert "false_golden_cross" in result.columns
    assert "false_death_cross" in result.columns
    assert len(result) == 30


def test_macd_above_zero_tracks_dif():
    index = pd.date_range("2024-01-01", periods=30)
    close = pd.Series([100 + i * 2 for i in range(30)], index=index)
    df = pd.DataFrame(
        {"open": close, "high": close + 2, "low": close - 2, "close": close, "vol": [1000000] * 30, "amount": [100000000] * 30},
        index=index,
    )
    result = add_macd(df)
    assert result["above_zero"].equals(result["dif"] > 0)


def test_macd_false_cross_not_simple_cross():
    index = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame(
        {
            "open": [100.0] * 30,
            "high": [102.0] * 30,
            "low": [98.0] * 30,
            "close": [100.0] * 30,
            "vol": [1000000] * 30,
            "amount": [100000000] * 30,
        },
        index=index,
    )
    result = add_macd(df)
    # Flat market: no cross events
    assert not result["false_golden_cross"].any()
    assert not result["false_death_cross"].any()


# ---------------------------------------------------------------------------
# 2026-09 审计：背离判据修正 + 向量化
# ---------------------------------------------------------------------------

def _series(n=400, seed=7):
    import numpy as np

    rng = np.random.default_rng(seed)
    close = np.clip(50 + np.cumsum(rng.normal(0, 1.0, n)), 5, None)
    prev = np.concatenate([[50.0], close[:-1]])
    return pd.DataFrame({
        "open": prev, "high": np.maximum(close, prev) * 1.01,
        "low": np.minimum(close, prev) * 0.99, "close": close,
        "vol": 1e6, "amount": 1e8,
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


def _legacy_bull(close, dif, w):
    """原实现的底背离（那一支本来就是对的），用来验证向量化没跑偏。"""
    out = pd.Series(False, index=close.index)
    for i in range(w, len(close)):
        if (float(close.iloc[i]) < float(close.iloc[i - w:i].min())
                and float(dif.iloc[i]) > float(dif.iloc[i - w:i].min())):
            out.iloc[i] = True
    return out


def test_vectorised_bull_divergence_matches_the_original_loop():
    df = _series()
    r = add_macd(df, divergence_window=60)
    close = pd.to_numeric(df["close"])
    dif = (close.ewm(span=12, adjust=False).mean()
           - close.ewm(span=26, adjust=False).mean())
    assert r["bull_divergence"].equals(_legacy_bull(close, dif, 60))


def test_bear_divergence_compares_against_previous_highs():
    """顶背离 = 价格创新高、DIF 不创新高。

    原实现拿窗口**最低点**当「前高」比——「今天收盘高于过去 60 天最低点」
    几乎恒为真，条件退化成了别的东西，而 macd_veto 正拿它否决买入信号。
    """
    df = _series()
    w = 60
    r = add_macd(df, divergence_window=w)
    close = pd.to_numeric(df["close"])
    dif = (close.ewm(span=12, adjust=False).mean()
           - close.ewm(span=26, adjust=False).mean())
    prev_high_p = close.shift(1).rolling(w).max()
    prev_high_d = dif.shift(1).rolling(w).max()
    for ts in r.index[r["bear_divergence"]]:
        assert close[ts] > prev_high_p[ts], "顶背离要求价格创新高"
        assert dif[ts] < prev_high_d[ts], "顶背离要求 DIF 不创新高"


def test_bull_divergence_requires_a_new_price_low():
    df = _series()
    w = 60
    r = add_macd(df, divergence_window=w)
    close = pd.to_numeric(df["close"])
    prev_low = close.shift(1).rolling(w).min()
    for ts in r.index[r["bull_divergence"]]:
        assert close[ts] < prev_low[ts], "底背离要求价格创新低"


def test_divergence_ignores_the_warmup_window():
    df = _series(n=120)
    r = add_macd(df, divergence_window=60)
    assert not r["bull_divergence"].iloc[:60].any()
    assert not r["bear_divergence"].iloc[:60].any()


def test_divergence_columns_are_boolean_not_nan():
    """NaN 进了布尔列，下游 `if row['bear_divergence']` 会按 True 处理。"""
    r = add_macd(_series(n=80), divergence_window=60)
    for col in ("bull_divergence", "bear_divergence"):
        assert r[col].dtype == bool
        assert not r[col].isna().any()


def test_add_macd_is_fast_enough_for_repeated_backtests():
    """消融和参数校准动辄跑几十次回测，这里慢一点就是几分钟的差别。

    原实现单只 1200 根 K 线要 246ms；向量化后应在 30ms 以内。
    """
    import time

    df = _series(n=1200)
    t0 = time.perf_counter()
    add_macd(df, divergence_window=60)
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 30, f"add_macd 耗时 {ms:.0f}ms，背离那段大概又退回 Python 循环了"
