"""Base strategy abstract class and TradeSignal dataclass."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import pandas as pd


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_STOP = "time_stop"
    EOD = "eod"
    REVERSE_SIGNAL = "reverse_signal"
    FALSE_BREAKOUT = "false_breakout"
    SCALE_OUT_1 = "scale_out_1"
    SCALE_OUT_2 = "scale_out_2"
    ADAPTIVE_TRAILING = "adaptive_trailing"


@dataclass
class ScaleLevel:
    """Defines a scale-out level for partial position exits."""
    pct_to_close: float              # fraction of original qty to close (e.g. 0.50)
    atr_profit_multiple: float       # trigger when profit >= this * ATR
    move_stop_to_breakeven: bool     # move effective stop to entry after this scale
    new_trailing_atr_mult: Optional[float] = None  # tighten trailing to this * ATR


@dataclass
class TradeSignal:
    strategy: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int = 0  # calculated by risk manager
    confidence: float = 0.5
    timestamp: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ExitSignal:
    reason: ExitReason
    exit_price: float
    timestamp: Optional[datetime] = None
    quantity: Optional[int] = None  # None = close entire position


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    name: str = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params = params or self.default_params()

    @abstractmethod
    def default_params(self) -> dict:
        ...

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        """Check if entry conditions are met at bar index `idx`."""
        ...

    @abstractmethod
    def should_exit(
        self,
        df: pd.DataFrame,
        idx: int,
        trade: TradeSignal,
        entry_time: datetime,
        current_time: datetime,
        highest_since_entry: float,
        lowest_since_entry: float,
    ) -> Optional[ExitSignal]:
        """Check if exit conditions are met for an open trade."""
        ...
