"""Base strategy abstract class and TradeSignal dataclass."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.regime_detector import MarketRegime


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
class MarketContext:
    """Bundles all timeframe data + regime info for multi-timeframe analysis."""
    df_1min: pd.DataFrame
    df_5min: pd.DataFrame
    df_15min: pd.DataFrame
    df_30min: pd.DataFrame
    df_1hr: pd.DataFrame
    df_4hr: pd.DataFrame
    regime: MarketRegime


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

    @staticmethod
    def compute_confluence_score(ctx: MarketContext, direction: Direction) -> float:
        """Compute multi-timeframe confluence score (0-100).

        Evaluates trend alignment, volume confirmation, key level proximity,
        risk/reward quality, and volatility regime across all timeframes.

        Returns a score 0-100 that maps to signal confidence.
        """
        score = 0.0
        sign = 1.0 if direction == Direction.LONG else -1.0

        # ── 1. Trend alignment across timeframes (60 pts total) ──
        tf_weights = [
            (ctx.df_4hr, 25.0),
            (ctx.df_1hr, 20.0),
            (ctx.df_30min, 15.0),
            (ctx.df_15min, 12.0),
            (ctx.df_5min, 5.0),
            (ctx.df_1min, 3.0),
        ]
        for df_tf, weight in tf_weights:
            if df_tf is None or df_tf.empty or len(df_tf) < 5:
                continue
            row = df_tf.iloc[-1]
            ema9 = row.get("ema9")
            ema21 = row.get("ema21")
            if ema9 is not None and ema21 is not None and not pd.isna(ema9) and not pd.isna(ema21):
                if (ema9 > ema21 and sign > 0) or (ema9 < ema21 and sign < 0):
                    score += weight
                elif (ema9 < ema21 and sign > 0) or (ema9 > ema21 and sign < 0):
                    score -= weight * 0.5  # Penalize counter-trend

        # ── 2. Volume confirmation (10 pts) ──
        if ctx.df_1min is not None and not ctx.df_1min.empty:
            row_1m = ctx.df_1min.iloc[-1]
            vol_ratio = row_1m.get("vol_ratio", 1.0)
            if vol_ratio is not None and not pd.isna(vol_ratio):
                vol_ratio = float(vol_ratio)
                if vol_ratio >= 1.5:
                    score += 10.0
                elif vol_ratio >= 1.2:
                    score += 6.0
                elif vol_ratio >= 1.0:
                    score += 3.0

        # ── 3. Key level proximity — VWAP alignment (10 pts) ──
        if ctx.df_1min is not None and not ctx.df_1min.empty:
            row_1m = ctx.df_1min.iloc[-1]
            close = row_1m.get("close")
            vwap = row_1m.get("vwap")
            if close is not None and vwap is not None and not pd.isna(close) and not pd.isna(vwap):
                close, vwap = float(close), float(vwap)
                if (close > vwap and sign > 0) or (close < vwap and sign < 0):
                    score += 10.0
                elif (close < vwap and sign > 0) or (close > vwap and sign < 0):
                    score -= 5.0

        # ── 4. Volatility regime check (10 pts) ──
        # Avoid extremes: top 5% or bottom 5% of ATR percentile rank
        if ctx.df_1hr is not None and not ctx.df_1hr.empty and len(ctx.df_1hr) > 20:
            atr_series = ctx.df_1hr["atr"].dropna()
            if len(atr_series) > 10:
                current_atr = float(atr_series.iloc[-1])
                pct_rank = (atr_series < current_atr).sum() / len(atr_series)
                if 0.05 <= pct_rank <= 0.95:
                    score += 10.0
                elif 0.10 <= pct_rank <= 0.90:
                    score += 5.0
                # Extreme volatility: no bonus (score += 0)

        # ── 5. MACD confirmation across higher TFs (10 pts) ──
        htf_macd = [(ctx.df_1hr, 5.0), (ctx.df_4hr, 5.0)]
        for df_tf, weight in htf_macd:
            if df_tf is None or df_tf.empty:
                continue
            macd_hist = df_tf.iloc[-1].get("macd_hist")
            if macd_hist is not None and not pd.isna(macd_hist):
                if (float(macd_hist) > 0 and sign > 0) or (float(macd_hist) < 0 and sign < 0):
                    score += weight

        return max(0.0, min(100.0, score))
