"""Tests for position sizer."""

import pytest

from zhixing_quant.portfolio.sizer import PositionSizer


def test_sizer_returns_lot_size_multiple():
    sizer = PositionSizer()
    shares = sizer.size(entry_price=10.0, stop_loss=9.0, equity=100000.0)
    assert shares % 100 == 0


def test_sizer_returns_zero_when_no_risk():
    sizer = PositionSizer()
    shares = sizer.size(entry_price=10.0, stop_loss=10.0, equity=100000.0)
    assert shares == 0


def test_sizer_respects_regime_cap():
    sizer = PositionSizer()
    shares_cap = sizer.size(entry_price=10.0, stop_loss=9.0, equity=100000.0, regime_max_pct=0.1)
    shares_full = sizer.size(entry_price=10.0, stop_loss=9.0, equity=100000.0, regime_max_pct=1.0)
    assert shares_cap <= shares_full
