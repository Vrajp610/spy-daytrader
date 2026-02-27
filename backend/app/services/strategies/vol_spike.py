"""Volatility Spike Scalping strategy.

Strategy spec:
- Monitor implied volatility and SPY's intraday IV percentile.
- When SPY experiences a rapid price move AND IV spikes into the top 80 % of its
  30-day range, buy straddles or strangles and exit once premium expands by
  15–20 % or IV reverts.
- Use 0–1 DTE options with 0.40–0.50 delta calls and puts.
- Limit risk to 1 % of equity.
- Avoid trading when VIX > 30 (already too expensive) or < 15 (too cheap, no spike).

Entry conditions:
  1. IV rank > 80 % (spike into top quintile of 30-day range).
  2. Intraday ATR expansion: current ATR > 1.5× recent average.
  3. Rapid directional move: |ROC5| on 1-min bars > 0.3 %.
  4. VIX in 15–30 range (measured by engine, passed via market_context.vix).
  5. Time window: 9:45 AM – 14:30 PM.

Exit:
  - Options exit rules for LONG_STRADDLE: take profit at 80 % of max gain,
    stop at 40 % loss of entry premium (conservative for volatile regime).
  - Force exit by 14:30 PM to avoid gamma risk close to 0-DTE expiry.
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class VolSpikeStrategy(BaseStrategy):
    name = "vol_spike"

    def default_params(self) -> dict:
        return {
            "iv_rank_min": 70.0,          # IV rank threshold (top 30%)
            "atr_expansion_ratio": 1.5,  # current ATR > 1.5× recent average
            "atr_lookback": 20,           # bars for ATR baseline
            "roc_threshold": 0.30,        # minimum |ROC5| on 1-min (0.30%)
            "vix_min": 15.0,              # VIX floor: don't trade when too calm
            "vix_max": 30.0,              # VIX cap: too fearful = premium too expensive
            "min_entry_time": "09:45",
            "max_entry_time": "14:30",
            "eod_exit_time": "14:30",     # aggressive exit before close
            "target_delta": 0.45,         # near-ATM for straddle legs
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 25:
            return None

        p = self.params
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        min_time = time(*[int(x) for x in p["min_entry_time"].split(":")])
        max_time = time(*[int(x) for x in p["max_entry_time"].split(":")])
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])

        if t < min_time or t >= max_time or t >= eod:
            return None

        # Get VIX and IV rank from market context
        ctx = kwargs.get("market_context")
        vix = getattr(ctx, "vix", 20.0) if ctx is not None else 20.0
        iv_rank = getattr(ctx, "iv_rank", 50.0) if ctx is not None else 50.0

        # VIX gate: must be in the 15–30 range
        if not (p["vix_min"] <= vix <= p["vix_max"]):
            return None

        # IV rank must be in the top threshold (spike)
        if iv_rank < p["iv_rank_min"]:
            return None

        row = df.iloc[idx]
        close = row.get("close")
        atr = row.get("atr")
        roc5 = row.get("roc5")

        if any(pd.isna(v) or v is None for v in [close, atr]):
            return None

        close, atr = float(close), float(atr)

        # ATR expansion check: current ATR vs. rolling baseline
        lookback = min(p["atr_lookback"], idx)
        atr_baseline = df.iloc[idx - lookback:idx]["atr"].mean()
        if pd.isna(atr_baseline) or atr_baseline <= 0:
            return None
        if atr < atr_baseline * p["atr_expansion_ratio"]:
            return None

        # Rapid directional move check
        roc5_val = float(roc5) if roc5 is not None and not pd.isna(roc5) else 0.0
        if abs(roc5_val) < p["roc_threshold"]:
            return None

        # Straddle is direction-neutral (we're buying volatility, not direction)
        # Use direction = LONG as a convention for buying the straddle (long vega)
        iv_bonus = min(0.10, (iv_rank - p["iv_rank_min"]) / 20.0 * 0.10)
        atr_bonus = min(0.05, (atr / atr_baseline - p["atr_expansion_ratio"]) * 0.05)
        confidence = min(0.80, 0.60 + iv_bonus + atr_bonus)

        # Use ATR-based stop/target as rough guide; straddle profit is premium-driven
        stop = close - atr * 1.5
        target = close + atr * 2.0

        # Direction hint for the straddle: go with the momentum direction
        direction = Direction.LONG if roc5_val >= 0 else Direction.SHORT

        return TradeSignal(
            strategy=self.name,
            direction=direction,
            entry_price=close,
            stop_loss=stop,
            take_profit=target,
            confidence=confidence,
            timestamp=current_time,
            metadata={
                "iv_rank": round(iv_rank, 1),
                "vix": round(vix, 1),
                "atr_ratio": round(atr / atr_baseline, 2),
                "roc5": round(roc5_val, 3),
                # Buy a straddle (both call and put) to profit from the spike
                "options_preference": "straddle",
                "preferred_dte": 1,          # 0–1 DTE for maximum gamma sensitivity
                "min_dte": 0,
                "target_delta": p["target_delta"],  # 0.45 (near-ATM)
                "fallback_delta": 0.40,
            },
        )

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

        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.TIME_STOP, exit_price=close, timestamp=current_time)

        # Straddle: underlying stop not the primary exit — options engine handles premium
        is_long = trade.direction == Direction.LONG
        if is_long and close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        return None
