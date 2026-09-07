"""Tests for brick strategy entry/exit."""

import pandas as pd

from zhixing_quant.strategies.brick_strategy import BrickStrategy


def _brick_df(sig=True):
    index = pd.date_range("2024-01-01", periods=10)
    close = [100.0 + i for i in range(10)]
    df = pd.DataFrame(
        {
            "open": [c - 1.0 for c in close],
            "high": [c + 2.0 for c in close],
            "low": [c - 2.0 for c in close],
            "close": close,
            "vol": [1000000] * 10,
            "yellow_line": [99.0] * 10,
            "sig_brick": [False] * 9 + [sig],
            "stop_loss": [c - 3.0 for c in close],
        },
        index=index,
    )
    df.index.name = "date"
    return df


def test_brick_strategy_entry_when_signal():
    strategy = BrickStrategy({})
    df = _brick_df(sig=True)
    entry = strategy.entry_conditions(df, len(df) - 1, {})
    assert entry is not None
    assert entry.strategy == "brick"
    assert entry.confidence == 4


def test_brick_strategy_no_entry_when_no_signal():
    strategy = BrickStrategy({})
    df = _brick_df(sig=False)
    entry = strategy.entry_conditions(df, len(df) - 1, {})
    assert entry is None


def test_brick_strategy_exit_on_stop():
    strategy = BrickStrategy({})
    df = _brick_df(sig=True)
    pos = {"code": "600519", "stop_loss": 97.0, "take_profit": 200.0}
    df.loc[df.index[-1], "low"] = 96.0
    exit_sig = strategy.exit_conditions(df, len(df) - 1, pos, {})
    assert exit_sig is not None
    assert exit_sig.priority == 2
