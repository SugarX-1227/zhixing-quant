"""Tests for backtest models."""

import pytest

from zhixing_quant.backtest.models import calc_slippage, calc_limit_price, is_limit_up, is_limit_down


def test_slippage_pct_model():
    slip = calc_slippage(10.0, "BUY", "pct")
    assert slip == pytest.approx(0.01)


def test_slippage_fixed_model():
    slip = calc_slippage(10.0, "SELL", "fixed")
    assert slip == 0.01


def test_limit_price_main_board():
    up, down = calc_limit_price(10.0, 0.10)
    assert up == 11.0
    assert down == 9.0


def test_limit_price_chinext():
    up, down = calc_limit_price(10.0, 0.20)
    assert up == 12.0
    assert down == 8.0


def test_is_limit_up_true():
    assert is_limit_up(11.0, 11.0) is True


def test_is_limit_down_true():
    assert is_limit_down(9.0, 9.0) is True
