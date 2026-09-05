"""Tests for B2 strategy entry modes."""

import pandas as pd

from zhixing_quant.strategies.b2_strategy import B2Strategy


def _b2_df(sig=True, vol_ratio=1.0):
    index = pd.date_range("2024-01-01", periods=25)
    close = [100.0 + i * 0.3 for i in range(25)]
    df = pd.DataFrame(
        {
            "open": [c - 0.5 for c in close],
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "vol": [1000000] * 24 + [int(1000000 * vol_ratio)],
            "yellow_line": [99.0] * 25,
            "white_line": [101.0] * 25,
            "sig_b2": [False] * 24 + [sig],
        },
        index=index,
    )
    df.index.name = "date"
    return df


def test_b2_heavy_cannon_mode():
    strategy = B2Strategy({})
    df = _b2_df(sig=True, vol_ratio=2.5)
    entry = strategy.entry_conditions(df, len(df) - 1, {})
    assert entry is not None
    assert "parallel_heavy_cannon" in entry.strategy
    assert entry.confidence == 5


def test_b2_no_entry_without_signal():
    strategy = B2Strategy({})
    df = _b2_df(sig=False)
    entry = strategy.entry_conditions(df, len(df) - 1, {})
    assert entry is None
