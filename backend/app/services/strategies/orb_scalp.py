"""Opening-Range Breakout Scalping strategy.

Strategy spec (quantvps.com):
- Define 9:30–9:45 AM range (15-min opening range).
- Enter when a 1-min candle closes above/below the range with volume > 150 % of
  the 10-day average.  Uses ATM/near-ATM options with 0–2 DTE.
- Target +20–40 % premium gain (handled by options exit rules for LONG_CALL/PUT).
- Stop  −15–20 % premium loss (handled by options exit rules).
- Exit all positions by 10:30 AM regardless.
- Risk ≤ 2 % per trade.  Cap total exposure to breakout strategies at 5 %.

Differences from the existing ORB strategy:
  - Targets 0–2 DTE long options (long_call / long_put) instead of credit spreads.
  - Tighter entry window exits at 10:30 (not 10:30 entry cutoff — we force exit).
  - Requires higher initial volume conviction (same 1.5× spec threshold).
  - Near-ATM delta 0.45 for max leverage on the breakout move.
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class ORBScalpStrategy(BaseStrategy):
    name = "orb_scalp"

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self._opening_ranges: dict[str, dict] = {}  # date_str -> {high, low, range_width}

    def default_params(self) -> dict:
        return {
            "opening_range_minutes": 15,    # 9:30–9:45 AM
            "min_range_pct": 0.0010,        # min 0.10% range width (tighter than orb)
            "max_range_pct": 0.006,         # max 0.60% (avoid extreme gaps)
            "volume_surge_ratio": 1.5,      # 150% of moving average (spec requirement)
            "target_range_mult": 1.5,       # 1.5× range width target (conservative for scalp)
            "retracement_stop_pct": 0.50,   # stop at 50% retracement of range
            "max_entry_time": "10:20",      # no new entries after 10:20 AM
            "force_exit_time": "10:30",     # force close all positions by 10:30 AM
            "eod_exit_time": "15:55",
            "false_breakout_bars": 3,
        }

    def _get_opening_range(
        self, df: pd.DataFrame, idx: int, current_time: datetime
    ) -> Optional[dict]:
        """Calculate the 9:30–9:45 opening range for today."""
        date_str = current_time.strftime("%Y-%m-%d")
        if date_str in self._opening_ranges:
            return self._opening_ranges[date_str]

        or_end = time(9, 45)
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        if t < or_end:
            return None

        or_start = time(9, 30)
        or_bars = []
        for i in range(max(0, idx - 60), idx + 1):
            bar_time = df.index[i]
            bt = bar_time.time() if hasattr(bar_time, "time") else bar_time
            bar_date = bar_time.date() if hasattr(bar_time, "date") else None
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
        range_pct = range_width / or_low if or_low > 0 else 0

        p = self.params
        if range_pct < p["min_range_pct"] or range_pct > p["max_range_pct"]:
            self._opening_ranges[date_str] = None
            return None

        result = {
            "high": or_high,
            "low": or_low,
            "range_width": range_width,
            "range_pct": range_pct,
        }
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
        force_exit = time(*[int(x) for x in p["force_exit_time"].split(":")])

        # Only trade between 9:45 AM and 10:20 AM
        if t >= max_entry or t >= force_exit or t < time(9, 45):
            return None

        or_data = self._get_opening_range(df, idx, current_time)
        if or_data is None:
            return None

        close = float(row["close"])
        vol_ratio = row.get("vol_ratio", 1.0)
        if pd.isna(vol_ratio) or float(vol_ratio) < p["volume_surge_ratio"]:
            return None

        or_high = or_data["high"]
        or_low = or_data["low"]
        range_width = or_data["range_width"]

        atr = row.get("atr")
        atr_val = float(atr) if atr is not None and not pd.isna(atr) else range_width

        # Quality score: volume surge + range quality
        vol_score = min(1.0, (float(vol_ratio) - 1.5) / 1.5)  # 0 at 1.5×, 1 at 3.0×
        range_quality = (range_width / atr_val) if atr_val > 0 else 0.5
        confidence = min(0.85, 0.60 + vol_score * 0.15 + min(0.10, range_quality * 0.05))

        meta = {
            "or_high": or_high,
            "or_low": or_low,
            "range_width": range_width,
            # Options configuration: near-ATM 0–2 DTE long option for max breakout leverage
            "options_preference": "long_call",   # overridden to long_put for SHORT below
            "preferred_dte": 1,                  # target next-day / 0-DTE expiration
            "min_dte": 0,                        # allow 0-DTE (same-day expiry)
            "target_delta": 0.45,               # near-ATM for breakout
            "fallback_delta": 0.40,
        }

        # Breakout above opening range
        if close > or_high:
            stop = or_high - range_width * p["retracement_stop_pct"]
            target = close + range_width * p["target_range_mult"]
            meta["options_preference"] = "long_call"
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata=meta,
            )

        # Breakdown below opening range
        if close < or_low:
            stop = or_low + range_width * p["retracement_stop_pct"]
            target = close - range_width * p["target_range_mult"]
            meta["options_preference"] = "long_put"
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata=meta,
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
        close = float(row["close"])
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        # Force exit by 10:30 AM — this is a pure scalp strategy
        force_exit = time(*[int(x) for x in p["force_exit_time"].split(":")])
        if t >= force_exit:
            return ExitSignal(reason=ExitReason.TIME_STOP, exit_price=close, timestamp=current_time)

        # EOD exit as fallback
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.EOD, exit_price=close, timestamp=current_time)

        is_long = trade.direction == Direction.LONG

        # Stop loss (underlying-based, options engine handles premium stops separately)
        if is_long and close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        # Take profit
        if is_long and close >= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)
        if not is_long and close <= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)

        # False breakout: price returns inside range quickly
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

            if 2 <= bars_since <= p["false_breakout_bars"]:
                if is_long and close < or_data.get("or_high", float("inf")):
                    return ExitSignal(reason=ExitReason.FALSE_BREAKOUT, exit_price=close, timestamp=current_time)
                if not is_long and close > or_data.get("or_low", 0):
                    return ExitSignal(reason=ExitReason.FALSE_BREAKOUT, exit_price=close, timestamp=current_time)

        return None
