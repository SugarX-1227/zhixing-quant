"""战法注册表。

新增战法的三步：
    1. 在 strategies/ 下实现 Strategy 子类
    2. 在本文件末尾 register_strategy 注册
    3. 在 indicators/pipeline.py 的 PIPELINES 里声明它需要哪些指标

做完这三步，扫描器、今日页、回测页会自动认得它，不需要再写 scanner/daily_*.py。
"""

from __future__ import annotations

from typing import Optional

from zhixing_quant.strategies.b1_strategy import B1Strategy
from zhixing_quant.strategies.b2_strategy import B2Strategy
from zhixing_quant.strategies.base import Strategy
from zhixing_quant.strategies.brick_strategy import BrickStrategy
from zhixing_quant.strategies.single_needle_strategy import SingleNeedleStrategy

STRATEGY_REGISTRY: dict = {}


def register_strategy(name: str, strategy_cls):
    """注册一个战法类。"""
    STRATEGY_REGISTRY[name] = strategy_cls


def get_strategy(name: str, cfg: dict) -> Optional[Strategy]:
    """按名字实例化战法。未注册返回 None。"""
    cls = STRATEGY_REGISTRY.get(name)
    return cls(cfg) if cls else None


def list_strategies(filter_regime: str = None) -> list:
    """列出可用战法，可按择时区间过滤。"""
    result = []
    for name, cls in STRATEGY_REGISTRY.items():
        if filter_regime:
            if filter_regime not in cls({}).allowed_regimes:
                continue
        result.append(name)
    return result


register_strategy("brick", BrickStrategy)
register_strategy("b1", B1Strategy)
register_strategy("b2", B2Strategy)
register_strategy("single_needle", SingleNeedleStrategy)
