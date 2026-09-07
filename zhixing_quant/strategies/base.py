"""Base strategy ABC: entry/exit conditions interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class EntrySignal:
    code: str
    date: str
    strategy: str
    price: float
    stop_loss: float
    take_profit: float
    confidence: int  # 0-5
    reason: str


@dataclass
class ExitSignal:
    code: str
    date: str
    price: float
    reason: str
    priority: int


class Strategy(ABC):
    """Abstract base class for all trading strategies."""

    name: str = "base"
    allowed_regimes: list = []

    @abstractmethod
    def entry_conditions(self, df: pd.DataFrame, idx: int, cfg: dict) -> Optional[EntrySignal]:
        """Check if entry conditions are met at idx."""

    @abstractmethod
    def exit_conditions(self, df: pd.DataFrame, idx: int, pos: dict, cfg: dict) -> Optional[ExitSignal]:
        """Check if exit conditions are met at idx."""
