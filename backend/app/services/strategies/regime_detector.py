"""Market regime detection using ADX, ATR, VWAP alignment, and Bollinger Band width."""

from __future__ import annotations
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE_BOUND = "RANGE_BOUND"
    VOLATILE = "VOLATILE"


class RegimeDetector:
    """Classifies the current market regime on 5-min bars."""

    def __init__(self, params: Optional[dict] = None):
        defaults = {
            "adx_trend_threshold": 25,
            "adx_range_threshold": 18,
            "bb_width_volatile_threshold": 0.025,
            "bb_width_range_threshold": 0.012,
            "atr_lookback": 20,
            "atr_volatile_multiplier": 1.5,
            "lookback_bars": 20,
        }
        self.params = {**defaults, **(params or {})}

    def detect(self, df: pd.DataFrame, idx: int) -> MarketRegime:
        """Determine market regime at bar index `idx`."""
        p = self.params
        lookback = p["lookback_bars"]

        if idx < lookback:
            return MarketRegime.RANGE_BOUND

        row = df.iloc[idx]

        adx = row.get("adx", 20)
        bb_width = row.get("bb_width", 0.015)
        close = row["close"]
        vwap = row.get("vwap", close)

        # ATR volatility check: compare current ATR to historical median
        atr_slice = df["atr"].iloc[max(0, idx - lookback):idx + 1]
        atr_now = row.get("atr", 0)
        atr_median = atr_slice.median() if len(atr_slice) > 0 else atr_now
        atr_elevated = atr_now > atr_median * p["atr_volatile_multiplier"]

        # Volatile regime
        if bb_width > p["bb_width_volatile_threshold"] and atr_elevated:
            return MarketRegime.VOLATILE

        # Trending regime
        if adx > p["adx_trend_threshold"]:
            # Determine direction via VWAP alignment and recent closes
            recent_closes = df["close"].iloc[max(0, idx - 5):idx + 1]
            above_vwap = close > vwap
            rising = recent_closes.iloc[-1] > recent_closes.iloc[0] if len(recent_closes) > 1 else True

            if above_vwap and rising:
                return MarketRegime.TRENDING_UP
            elif not above_vwap and not rising:
                return MarketRegime.TRENDING_DOWN
            # Ambiguous trend - still call it trending based on VWAP
            return MarketRegime.TRENDING_UP if above_vwap else MarketRegime.TRENDING_DOWN

        # Range-bound
        if adx < p["adx_range_threshold"] and bb_width < p["bb_width_range_threshold"]:
            return MarketRegime.RANGE_BOUND

        # Default to range-bound for ambiguous states
        return MarketRegime.RANGE_BOUND
