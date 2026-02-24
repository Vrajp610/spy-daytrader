"""Smart Money Concepts — Supply & Demand Zones (FluidTrades SMC Lite).

SMC identifies where institutional players left footprints:
  DEMAND ZONE: area where price previously reversed UP sharply (strong buying)
  SUPPLY ZONE: area where price previously reversed DOWN sharply (strong selling)

When price retests these zones, institutions often re-enter, creating
high-probability reversals.

Implementation:
  - Detect swing lows/highs over a lookback window (20 bars)
  - A demand zone = swing low + one bar of consolidation before an impulsive up move
  - A supply zone = swing high + one bar of consolidation before an impulsive down move
  - Entry: price revisits within 0.25 ATR of the zone + RSI confirmation

Exit: 1.8x ATR target | 1.2x ATR stop | zone invalidated | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class SMCSupplyDemandStrategy(BaseStrategy):
    name = "smc_supply_demand"

    def default_params(self) -> dict:
        return {
            "swing_lookback":    20,    # bars to look back for swing H/L
            "zone_tolerance":    0.25,  # ATR multiple for zone proximity
            "impulse_min_atr":   1.5,   # minimum impulsive move (ATR multiples)
            "rsi_long_max":      55,    # RSI must be below for demand zone entry
            "rsi_short_min":     45,    # RSI must be above for supply zone entry
            "atr_target_mult":   1.8,
            "atr_stop_mult":     1.2,
            "atr_trailing_mult": 1.0,
            "eod_exit_time":    "15:55",
        }

    @staticmethod
    def _find_zones(
        df: pd.DataFrame,
        idx: int,
        lookback: int,
        impulse_min_atr: float,
    ) -> tuple[list[float], list[float]]:
        """Return (demand_levels, supply_levels) from swing analysis."""
        start = max(1, idx - lookback)
        segment = df.iloc[start:idx]
        if len(segment) < 4:
            return [], []

        demand_levels = []
        supply_levels = []

        closes = segment["close"].values
        highs  = segment["high"].values
        lows   = segment["low"].values
        atrs   = segment["atr"].values

        for i in range(1, len(segment) - 1):
            atr = atrs[i] if atrs[i] > 0 else 0.5
            # Swing low (demand): local minimum with impulsive rally after
            if (lows[i] < lows[i - 1] and lows[i] < lows[i + 1]
                    and closes[i + 1] - closes[i] > impulse_min_atr * atr):
                demand_levels.append(lows[i])
            # Swing high (supply): local maximum with impulsive drop after
            if (highs[i] > highs[i - 1] and highs[i] > highs[i + 1]
                    and closes[i] - closes[i + 1] > impulse_min_atr * atr):
                supply_levels.append(highs[i])

        return demand_levels, supply_levels

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        p   = self.params
        if idx < p["swing_lookback"] + 5:
            return None

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(10, 0) or t >= eod:
            return None

        row   = df.iloc[idx]
        close = row["close"]
        rsi   = row.get("rsi")
        atr   = row.get("atr")
        vwap  = row.get("vwap")

        for val in [rsi, atr, vwap]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        demand_levels, supply_levels = self._find_zones(
            df, idx, p["swing_lookback"], p["impulse_min_atr"]
        )
        tol = p["zone_tolerance"] * atr

        # Price revisiting a demand zone → LONG
        for level in demand_levels:
            if abs(close - level) <= tol and rsi < p["rsi_long_max"]:
                stop   = level - atr * p["atr_stop_mult"]
                target = close + atr * p["atr_target_mult"]
                confidence = min(0.88, 0.55 + (p["rsi_long_max"] - rsi) * 0.003)
                return TradeSignal(
                    strategy=self.name, direction=Direction.LONG,
                    entry_price=close, stop_loss=stop, take_profit=target,
                    confidence=confidence, timestamp=current_time,
                    metadata={"zone": "demand", "level": round(level, 2),
                              "options_preference": "debit_spread", "suggested_dte": 7},
                )

        # Price revisiting a supply zone → SHORT
        for level in supply_levels:
            if abs(close - level) <= tol and rsi > p["rsi_short_min"]:
                stop   = level + atr * p["atr_stop_mult"]
                target = close - atr * p["atr_target_mult"]
                confidence = min(0.88, 0.55 + (rsi - p["rsi_short_min"]) * 0.003)
                return TradeSignal(
                    strategy=self.name, direction=Direction.SHORT,
                    entry_price=close, stop_loss=stop, take_profit=target,
                    confidence=confidence, timestamp=current_time,
                    metadata={"zone": "supply", "level": round(level, 2),
                              "options_preference": "debit_spread", "suggested_dte": 7},
                )

        return None

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
        p     = self.params
        row   = df.iloc[idx]
        close = row["close"]
        atr   = row.get("atr", 0) or 0

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(ExitReason.EOD, close, current_time)

        is_long = trade.direction == Direction.LONG

        if is_long and close <= trade.stop_loss:
            return ExitSignal(ExitReason.STOP_LOSS, trade.stop_loss, current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(ExitReason.STOP_LOSS, trade.stop_loss, current_time)
        if is_long and close >= trade.take_profit:
            return ExitSignal(ExitReason.TAKE_PROFIT, trade.take_profit, current_time)
        if not is_long and close <= trade.take_profit:
            return ExitSignal(ExitReason.TAKE_PROFIT, trade.take_profit, current_time)

        # Trailing stop
        trail = p["atr_trailing_mult"] * atr
        if is_long:
            ts = highest_since_entry - trail
            if ts > trade.stop_loss and close <= ts:
                return ExitSignal(ExitReason.TRAILING_STOP, close, current_time)
        else:
            ts = lowest_since_entry + trail
            if ts < trade.stop_loss and close >= ts:
                return ExitSignal(ExitReason.TRAILING_STOP, close, current_time)

        return None
