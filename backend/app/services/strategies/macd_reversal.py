"""MACD Reversal strategy.

Entry: MACD histogram at extreme value, then reverses direction for 3 consecutive bars
       with volume confirmation.
Regime: Volatile
Exit: 1.5x ATR target | 1.0x ATR stop | trailing 0.75x ATR | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class MACDReversalStrategy(BaseStrategy):
    name = "macd_reversal"

    def default_params(self) -> dict:
        return {
            "hist_extreme_lookback": 30,
            "hist_extreme_percentile": 85,
            "reversal_bars": 3,
            "volume_surge_ratio": 1.2,
            "atr_target_mult": 1.5,
            "atr_stop_mult": 1.0,
            "atr_trailing_mult": 0.75,
            "eod_exit_time": "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 40:
            return None

        p = self.params
        row = df.iloc[idx]
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        close = row["close"]
        macd_hist = row.get("macd_hist")
        atr = row.get("atr")
        vol_ratio = row.get("vol_ratio", 1.0)

        for val in [macd_hist, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        if pd.isna(vol_ratio) or vol_ratio < p["volume_surge_ratio"]:
            return None

        # Get recent histogram values for extreme detection
        lookback = min(p["hist_extreme_lookback"], idx)
        hist_values = df.iloc[idx - lookback:idx]["macd_hist"].dropna()
        if len(hist_values) < 15:
            return None

        extreme_high = hist_values.quantile(p["hist_extreme_percentile"] / 100)
        extreme_low = hist_values.quantile(1 - p["hist_extreme_percentile"] / 100)

        rev_bars = p["reversal_bars"]
        if idx < rev_bars + 2:
            return None

        # Bullish reversal: histogram was at extreme negative, now rising for N bars
        hist_was_extreme_neg = False
        for i in range(idx - rev_bars - 3, idx - rev_bars):
            if i >= 0:
                h = df.iloc[i].get("macd_hist")
                if h is not None and not pd.isna(h) and h <= extreme_low:
                    hist_was_extreme_neg = True
                    break

        if hist_was_extreme_neg:
            rising = True
            for i in range(idx - rev_bars + 1, idx + 1):
                curr_h = df.iloc[i].get("macd_hist")
                prev_h = df.iloc[i - 1].get("macd_hist")
                if curr_h is None or prev_h is None or pd.isna(curr_h) or pd.isna(prev_h):
                    rising = False
                    break
                if curr_h <= prev_h:
                    rising = False
                    break

            if rising:
                stop = close - p["atr_stop_mult"] * atr
                target = close + p["atr_target_mult"] * atr
                hist_pct = abs(macd_hist) / abs(extreme_low) if abs(extreme_low) > 0 else 0
                confidence = min(0.85, 0.5 + hist_pct * 0.3 + max(0, (vol_ratio - 1.2)) * 0.1)
                return TradeSignal(
                    strategy=self.name,
                    direction=Direction.LONG,
                    entry_price=close,
                    stop_loss=stop,
                    take_profit=target,
                    confidence=confidence,
                    timestamp=current_time,
                    metadata={"macd_hist": macd_hist, "reversal": "bullish", "options_preference": "debit_spread", "suggested_dte": 7, "suggested_delta": 0.45},
                )

        # Bearish reversal: histogram was at extreme positive, now falling for N bars
        hist_was_extreme_pos = False
        for i in range(idx - rev_bars - 3, idx - rev_bars):
            if i >= 0:
                h = df.iloc[i].get("macd_hist")
                if h is not None and not pd.isna(h) and h >= extreme_high:
                    hist_was_extreme_pos = True
                    break

        if hist_was_extreme_pos:
            falling = True
            for i in range(idx - rev_bars + 1, idx + 1):
                curr_h = df.iloc[i].get("macd_hist")
                prev_h = df.iloc[i - 1].get("macd_hist")
                if curr_h is None or prev_h is None or pd.isna(curr_h) or pd.isna(prev_h):
                    falling = False
                    break
                if curr_h >= prev_h:
                    falling = False
                    break

            if falling:
                stop = close + p["atr_stop_mult"] * atr
                target = close - p["atr_target_mult"] * atr
                hist_pct = abs(macd_hist) / abs(extreme_high) if abs(extreme_high) > 0 else 0
                confidence = min(0.85, 0.5 + hist_pct * 0.3 + max(0, (vol_ratio - 1.2)) * 0.1)
                return TradeSignal(
                    strategy=self.name,
                    direction=Direction.SHORT,
                    entry_price=close,
                    stop_loss=stop,
                    take_profit=target,
                    confidence=confidence,
                    timestamp=current_time,
                    metadata={"macd_hist": macd_hist, "reversal": "bearish", "options_preference": "debit_spread", "suggested_dte": 7, "suggested_delta": 0.45},
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
        atr = row.get("atr", 0)

        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.EOD, exit_price=close, timestamp=current_time)

        is_long = trade.direction == Direction.LONG

        if is_long and close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        if is_long and close >= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)
        if not is_long and close <= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)

        trailing_dist = p["atr_trailing_mult"] * atr
        if is_long:
            trailing_stop = highest_since_entry - trailing_dist
            if trailing_stop > trade.stop_loss and close <= trailing_stop:
                return ExitSignal(reason=ExitReason.TRAILING_STOP, exit_price=close, timestamp=current_time)
        else:
            trailing_stop = lowest_since_entry + trailing_dist
            if trailing_stop < trade.stop_loss and close >= trailing_stop:
                return ExitSignal(reason=ExitReason.TRAILING_STOP, exit_price=close, timestamp=current_time)

        return None
