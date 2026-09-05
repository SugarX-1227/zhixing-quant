"""Strategy registry: register and retrieve strategies by name."""

from __future__ import annotations

from typing import Optional

from zhixing_quant.strategies.base import Strategy
from zhixing_quant.strategies.brick_strategy import BrickStrategy
from zhixing_quant.strategies.b2_strategy import B2Strategy
from zhixing_quant.strategies.dual_line import DualLineStrategy
from zhixing_quant.strategies.yoga_pants import YogaPantsStrategy

STRATEGY_REGISTRY: dict = {}


def register_strategy(name: str, strategy_cls):
    """Register a strategy class under a name."""
    STRATEGY_REGISTRY[name] = strategy_cls


def get_strategy(name: str, cfg: dict) -> Optional[Strategy]:
    """Instantiate and return a strategy by name."""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        return None
    return cls(cfg)


def list_strategies(filter_regime: str = None) -> list:
    """List available strategy names, optionally filtered by regime support."""
    result = []
    for name, cls in STRATEGY_REGISTRY.items():
        if filter_regime:
            instance = cls({})
            if filter_regime not in instance.allowed_regimes:
                continue
        result.append(name)
    return result


# Register built-in strategies
register_strategy("brick", BrickStrategy)
register_strategy("b2", B2Strategy)
register_strategy("dual_line", DualLineStrategy)
register_strategy("yoga_pants", YogaPantsStrategy)
