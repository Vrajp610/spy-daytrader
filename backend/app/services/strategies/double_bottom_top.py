"""Double Bottom/Top strategy.

Entry: Two swing lows within 0.2% of each other + neckline breakout with volume confirm.
Regime: Range-bound
Exit: Pattern-height target | 1.0x ATR stop | trailing 0.75x ATR | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class DoubleBottomTopStrategy(BaseStrategy):
    name = "double_bottom_top"

    def default_params(self) -> dict:
        return {
            "swing_lookback": 30,
            "price_tolerance_pct": 0.002,
            "min_bars_between_swings": 5,
            "volume_surge_ratio": 1.2,
            "atr_stop_mult": 1.0,
            "atr_trailing_mult": 0.75,
            "eod_exit_time": "15:55",
        }

    def _find_swing_lows(self, df: pd.DataFrame, end_idx: int, lookback: int) -> list[tuple[int, float]]:
        """Find swing low points (local minima) in the lookback window."""
        swings = []
        start = max(2, end_idx - lookback)
        for i in range(start, end_idx - 1):
            low_i = df.iloc[i]["low"]
            prev_low = df.iloc[i - 1]["low"]
            prev2_low = df.iloc[i - 2]["low"]
            next_low = df.iloc[i + 1]["low"]
            if low_i <= prev_low and low_i <= prev2_low and low_i <= next_low:
                swings.append((i, float(low_i)))
        return swings

    def _find_swing_highs(self, df: pd.DataFrame, end_idx: int, lookback: int) -> list[tuple[int, float]]:
        """Find swing high points (local maxima) in the lookback window."""
        swings = []
        start = max(2, end_idx - lookback)
        for i in range(start, end_idx - 1):
            high_i = df.iloc[i]["high"]
            prev_high = df.iloc[i - 1]["high"]
            prev2_high = df.iloc[i - 2]["high"]
            next_high = df.iloc[i + 1]["high"]
            if high_i >= prev_high and high_i >= prev2_high and high_i >= next_high:
                swings.append((i, float(high_i)))
        return swings

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
        atr = row.get("atr")
        vol_ratio = row.get("vol_ratio", 1.0)

        if atr is None or pd.isna(atr):
            return None

        lookback = p["swing_lookback"]
        tol = p["price_tolerance_pct"]
        min_gap = p["min_bars_between_swings"]

        # Double bottom: two swing lows within tolerance, then breakout above neckline
        lows = self._find_swing_lows(df, idx, lookback)
        for i in range(len(lows)):
            for j in range(i + 1, len(lows)):
                idx_i, price_i = lows[i]
                idx_j, price_j = lows[j]
                if abs(idx_j - idx_i) < min_gap:
                    continue
                if abs(price_i - price_j) / min(price_i, price_j) <= tol:
                    # Found double bottom - neckline is max between the two lows
                    neckline = df.iloc[idx_i:idx_j + 1]["high"].max()
                    pattern_height = neckline - min(price_i, price_j)
                    if close > neckline and (not pd.isna(vol_ratio) and vol_ratio >= p["volume_surge_ratio"]):
                        stop = close - p["atr_stop_mult"] * atr
                        target = close + pattern_height
                        symmetry = 1.0 - abs(price_i - price_j) / (min(price_i, price_j) * tol) if tol > 0 else 0
                        symmetry = max(0, symmetry)
                        confidence = min(0.85, 0.5 + symmetry * 0.2 + max(0, (vol_ratio - 1.2)) * 0.1)
                        return TradeSignal(
                            strategy=self.name,
                            direction=Direction.LONG,
                            entry_price=close,
                            stop_loss=stop,
                            take_profit=target,
                            confidence=confidence,
                            timestamp=current_time,
                            metadata={"pattern": "double_bottom", "neckline": neckline},
                        )

        # Double top: two swing highs within tolerance, then breakdown below neckline
        highs = self._find_swing_highs(df, idx, lookback)
        for i in range(len(highs)):
            for j in range(i + 1, len(highs)):
                idx_i, price_i = highs[i]
                idx_j, price_j = highs[j]
                if abs(idx_j - idx_i) < min_gap:
                    continue
                if abs(price_i - price_j) / max(price_i, price_j) <= tol:
                    neckline = df.iloc[idx_i:idx_j + 1]["low"].min()
                    pattern_height = max(price_i, price_j) - neckline
                    if close < neckline and (not pd.isna(vol_ratio) and vol_ratio >= p["volume_surge_ratio"]):
                        stop = close + p["atr_stop_mult"] * atr
                        target = close - pattern_height
                        symmetry = 1.0 - abs(price_i - price_j) / (min(price_i, price_j) * tol) if tol > 0 else 0
                        symmetry = max(0, symmetry)
                        confidence = min(0.85, 0.5 + symmetry * 0.2 + max(0, (vol_ratio - 1.2)) * 0.1)
                        return TradeSignal(
                            strategy=self.name,
                            direction=Direction.SHORT,
                            entry_price=close,
                            stop_loss=stop,
                            take_profit=target,
                            confidence=confidence,
                            timestamp=current_time,
                            metadata={"pattern": "double_top", "neckline": neckline},
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
