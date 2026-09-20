import pandas as pd

from zhixing_quant.indicators.b2 import add_b2_indicators
from zhixing_quant.indicators.tdx import exist

CFG = {
    "b2": {
        "yellow_ma_windows": [14, 28, 57, 114],
        "rsi_n": 9,
        "exist_window": 3,
        "j_oversold": 13,
        "chg_min": 3.95,
        "j_threshold": 55,
    },
}


def test_exist_true_when_condition_within_window():
    cond = pd.Series([False, True, False, False])
    result = exist(cond, 3)
    assert bool(result.iloc[3])


def test_exist_false_when_condition_outside_window():
    cond = pd.Series([True, False, False, False])
    result = exist(cond, 3)
    assert not bool(result.iloc[3])


def test_b2_requires_oversold_history_volume_and_chg():
    index = pd.date_range("2024-01-01", periods=15)
    df = pd.DataFrame(
        {
            "open": [100.0] * 15,
            "high": [105.0] * 15,
            "low": [95.0] * 15,
            "close": [101.0 + i * 0.3 for i in range(15)],
            "vol": [1000000 + i * 100000 for i in range(15)],
            "amount": [100000000] * 15,
        },
        index=index,
    )

    result = add_b2_indicators(df, CFG)

    assert "kdj_j" in result.columns
    assert "kdj_k" in result.columns
    assert "kdj_d" in result.columns
    assert "j_was_oversold" in result.columns
    assert "volume_up" in result.columns
    assert "sig_b2" in result.columns
    assert len(result) == 15


def test_b2_not_triggered_when_no_oversold_history():
    index = pd.date_range("2024-01-01", periods=15)
    df = pd.DataFrame(
        {
            "open": [200.0] * 15,
            "high": [205.0] * 15,
            "low": [195.0] * 15,
            "close": [202.0] * 15,
            "vol": [1000000] * 15,
            "amount": [100000000] * 15,
        },
        index=index,
    )

    result = add_b2_indicators(df, CFG)

    assert not result["sig_b2"].any()


# ---------------------------------------------------------------------------
# 规划书 4.3.1 的收紧条件（默认关闭）
# ---------------------------------------------------------------------------

def _signal_frame():
    """造一段必然触发 B2 的行情：先跌出超卖，再放量大阳。"""
    index = pd.date_range("2024-01-01", periods=30)
    close = [100.0 - i * 2.0 for i in range(20)] + [62.0, 66.0, 70.0, 74.0, 78.0,
                                                    82.0, 86.0, 90.0, 94.0, 98.0]
    return pd.DataFrame(
        {
            "open": [c * 0.99 for c in close],
            "high": [c * 1.005 for c in close],
            "low": [c * 0.985 for c in close],
            "close": close,
            "vol": [1_000_000 + i * 50_000 for i in range(30)],
            "amount": [100_000_000] * 30,
        },
        index=index,
    )


def test_tightening_disabled_by_default_matches_raw_formula():
    """默认配置（缺这几个键）必须与显式关闭逐位相同。"""
    df = _signal_frame()
    off = {"b2": dict(CFG["b2"], b1_within_days=0, body_ratio_min=0.0)}
    assert add_b2_indicators(df, CFG)["sig_b2"].equals(
        add_b2_indicators(df, off)["sig_b2"]
    )
    assert bool(add_b2_indicators(df, CFG)["sig_b2"].any())


def test_b1_within_days_drops_signals_without_prior_b1():
    """没有前置 B1 时，b1_within_days>0 必须把信号全部滤掉。"""
    df = _signal_frame()
    # j_threshold=-1 让 B1 永不成立，B2 就应该一个都不剩
    cfg = {
        "b2": dict(CFG["b2"], b1_within_days=5),
        "b1": {"rsi_n": 9, "j_threshold": -1, "chg_min": -4, "chg_max": 4},
    }
    out = add_b2_indicators(df, cfg)
    assert not bool(out["sig_b2"].any())
    assert not bool(out["b1_recent"].any())


def test_body_ratio_min_drops_long_shadow_bars():
    """长上影的信号 K 线（实体占比小）会被滤掉。"""
    df = _signal_frame()
    base = add_b2_indicators(df, CFG)["sig_b2"]
    hit = base[base].index[0]
    # 把信号那天改成长上影：实体不变，最高价拉高一倍幅度
    df.loc[hit, "high"] = float(df.loc[hit, "close"]) * 1.5

    cfg = {"b2": dict(CFG["b2"], body_ratio_min=0.6)}
    out = add_b2_indicators(df, cfg)
    assert float(out.loc[hit, "body_ratio"]) < 0.6
    assert not bool(out.loc[hit, "sig_b2"])
    # 关掉这条门槛时它仍然是信号，证明差异只来自新条件
    assert bool(add_b2_indicators(df, CFG).loc[hit, "sig_b2"])


def test_body_ratio_min_drops_bearish_bar():
    """有符号实体：收盘低于开盘的 K 线一定出局。"""
    df = _signal_frame()
    base = add_b2_indicators(df, CFG)["sig_b2"]
    hit = base[base].index[0]
    df.loc[hit, "open"] = float(df.loc[hit, "close"]) * 1.02   # 高开低走

    cfg = {"b2": dict(CFG["b2"], body_ratio_min=0.1)}
    assert not bool(add_b2_indicators(df, cfg).loc[hit, "sig_b2"])
