"""Opening Range Breakout (ORB) strategy.

Setup: Capture first 15-min high/low (skip if range too narrow <0.15% or too wide >0.8%)
Entry: Price closes beyond range + volume >= 1.5x average + before 10:30 AM
Exit: 2x range-width target | 50% retracement stop | false breakout | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class ORBStrategy(BaseStrategy):
    name = "orb"

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self._opening_ranges: dict[str, dict] = {}  # date_str -> {high, low, range_width}

    def default_params(self) -> dict:
        return {
            "opening_range_minutes": 15,
            "min_range_pct": 0.0015,
            "max_range_pct": 0.008,
            "volume_surge_ratio": 1.5,
            "target_range_mult": 2.0,
            "retracement_stop_pct": 0.5,
            "max_entry_time": "10:30",
            "false_breakout_bars": 3,
            "eod_exit_time": "15:55",
        }

    def _get_opening_range(self, df: pd.DataFrame, idx: int, current_time: datetime) -> Optional[dict]:
        """Calculate or retrieve opening range for today."""
        date_str = current_time.strftime("%Y-%m-%d")
        if date_str in self._opening_ranges:
            return self._opening_ranges[date_str]

        # Need at least 15 min of data after 9:30
        or_end = time(9, 45)
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        if t < or_end:
            return None

        # Find bars in the opening range window
        or_start = time(9, 30)
        or_bars = []
        for i in range(max(0, idx - 60), idx + 1):
            bar_time = df.index[i]
            bt = bar_time.time() if hasattr(bar_time, 'time') else bar_time
            bar_date = bar_time.date() if hasattr(bar_time, 'date') else None
            curr_date = current_time.date() if isinstance(current_time, datetime) else None
            if bar_date != curr_date:
                continue
            if or_start <= bt < or_end:
                or_bars.append(i)

        if not or_bars:
            return None

        or_high = df.iloc[or_bars]["high"].max()
        or_low = df.iloc[or_bars]["low"].min()
        range_width = or_high - or_low
        range_pct = range_width / or_low

        p = self.params
        if range_pct < p["min_range_pct"] or range_pct > p["max_range_pct"]:
            self._opening_ranges[date_str] = None
            return None

        result = {"high": or_high, "low": or_low, "range_width": range_width, "range_pct": range_pct}
        self._opening_ranges[date_str] = result
        return result

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 20:
            return None

        p = self.params
        row = df.iloc[idx]
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        max_entry = time(*[int(x) for x in p["max_entry_time"].split(":")])
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= max_entry or t >= eod or t < time(9, 45):
            return None

        or_data = self._get_opening_range(df, idx, current_time)
        if or_data is None:
            return None

        close = row["close"]
        vol_ratio = row.get("vol_ratio", 1.0)

        if pd.isna(vol_ratio) or vol_ratio < p["volume_surge_ratio"]:
            return None

        or_high = or_data["high"]
        or_low = or_data["low"]
        range_width = or_data["range_width"]

        atr = row.get("atr")
        range_quality = (or_high - or_low) / atr if atr is not None and atr > 0 else 0
        confidence = min(0.85, 0.5 + max(0, (vol_ratio - 1.5)) * 0.1 + range_quality * 0.15)

        # Breakout above opening range
        if close > or_high:
            stop = or_high - range_width * p["retracement_stop_pct"]
            target = close + range_width * p["target_range_mult"]
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"or_high": or_high, "or_low": or_low, "range_width": range_width, "options_preference": "credit_spread", "suggested_dte": 10, "suggested_delta": 0.20},
            )

        # Breakdown below opening range
        if close < or_low:
            stop = or_low + range_width * p["retracement_stop_pct"]
            target = close - range_width * p["target_range_mult"]
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"or_high": or_high, "or_low": or_low, "range_width": range_width, "options_preference": "credit_spread", "suggested_dte": 10, "suggested_delta": 0.20},
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
        p = self.params
        row = df.iloc[idx]
        close = row["close"]

        # EOD exit
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.EOD, exit_price=close, timestamp=current_time)

        is_long = trade.direction == Direction.LONG

        # Stop loss
        if is_long and close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        # Take profit
        if is_long and close >= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)
        if not is_long and close <= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)

        # False breakout detection: price closes back inside range within N bars
        # Only check after we've been in the trade for at least 2 bars
        or_data = trade.metadata
        if or_data and entry_time:
            bars_since = 0
            for i in range(idx, max(0, idx - p["false_breakout_bars"] * 2), -1):
                bar_t = df.index[i]
                try:
                    if bar_t <= entry_time:
                        break
                except TypeError:
                    break
                bars_since += 1
                if bars_since > p["false_breakout_bars"]:
                    break

            # Must be at least 2 bars in, but not more than false_breakout_bars
            if 2 <= bars_since <= p["false_breakout_bars"]:
                if is_long and close < or_data.get("or_high", float("inf")):
                    return ExitSignal(reason=ExitReason.FALSE_BREAKOUT, exit_price=close, timestamp=current_time)
                if not is_long and close > or_data.get("or_low", 0):
                    return ExitSignal(reason=ExitReason.FALSE_BREAKOUT, exit_price=close, timestamp=current_time)

        return None
