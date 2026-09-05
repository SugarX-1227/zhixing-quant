"""Signal package: exports all buy/sell signal detectors."""

from __future__ import annotations

from zhixing_quant.signals.buy_b3 import detect_b3
from zhixing_quant.signals.buy_sb1 import detect_sb1
from zhixing_quant.signals.buy_violent_k import detect_violent_k
from zhixing_quant.signals.distribution import detect_distribution
from zhixing_quant.signals.macd_veto import macd_veto
from zhixing_quant.signals.sell_s import detect_s_series

__all__ = [
    "macd_veto",
    "detect_b3",
    "detect_sb1",
    "detect_violent_k",
    "detect_s_series",
    "detect_distribution",
]
