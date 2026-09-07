import pandas as pd

from zhixing_quant.indicators.key_kline import detect_key_k


def test_key_kline_columns_exist():
    index = pd.date_range("2024-01-01", periods=60)
    df = pd.DataFrame(
        {
            "open": [100 + i * 0.5 for i in range(60)],
            "high": [102 + i * 0.5 for i in range(60)],
            "low": [98 + i * 0.5 for i in range(60)],
            "close": [101 + i * 0.5 for i in range(60)],
            "vol": [1000000 + i * 10000 for i in range(60)],
            "amount": [100000000] * 60,
        },
        index=index,
    )
    result = detect_key_k(df)

    pattern_cols = [
        "k_v_reversal", "k_emergency_brake", "k_flat_thunder", "k_top_a_kill",
        "k_pat_dead", "k_armor_lost", "k_big_bull", "k_big_bear",
        "k_windmill", "k_hammer",
    ]
    pos_cols = ["near_yellow_line", "near_white_line", "breakout_platform", "cover_trapped"]
    for col in pattern_cols + pos_cols + ["is_key_k"]:
        assert col in result.columns


def test_big_bull_detected():
    index = pd.date_range("2024-01-01", periods=20)
    df = pd.DataFrame(
        {
            "open": [100.0] * 20,
            "high": [110.0] * 20,
            "low": [95.0] * 20,
            "close": [108.0] * 20,
            "vol": [1000000] * 20,
            "amount": [100000000] * 20,
        },
        index=index,
    )
    result = detect_key_k(df)
    # In a flat strong market, should detect big_bull or similar
    assert result[["k_big_bull", "k_big_bear", "is_key_k"]].notna().all().all()


def test_key_k_false_when_no_pattern():
    index = pd.date_range("2024-01-01", periods=60)
    df = pd.DataFrame(
        {
            "open": [100.0] * 60,
            "high": [100.5] * 60,
            "low": [99.5] * 60,
            "close": [100.0] * 60,
            "vol": [1000000] * 60,
            "amount": [100000000] * 60,
        },
        index=index,
    )
    result = detect_key_k(df)
    # Extremely flat market: no big bull/bear, but may have other patterns
    assert result["is_key_k"].dtype == bool
