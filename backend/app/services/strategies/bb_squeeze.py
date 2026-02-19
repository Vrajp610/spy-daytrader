"""Bollinger Band Squeeze strategy.

Entry: BB width contracts to bottom 20th percentile of recent history,
       then expands with volume surge → breakout trade.
Regime: Range-bound
Exit: 2.0x ATR target | 1.0x ATR stop | trailing 0.75x ATR | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class BBSqueezeStrategy(BaseStrategy):
    name = "bb_squeeze"

    def default_params(self) -> dict:
        return {
            "squeeze_percentile": 20,
            "squeeze_lookback": 50,
            "expansion_bars": 3,
            "volume_surge_ratio": 1.3,
            "atr_target_mult": 2.0,
            "atr_stop_mult": 1.0,
            "atr_trailing_mult": 0.75,
            "eod_exit_time": "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 60:
            return None

        p = self.params
        row = df.iloc[idx]
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        close = row["close"]
        bb_width = row.get("bb_width")
        bb_upper = row.get("bb_upper")
        bb_lower = row.get("bb_lower")
        atr = row.get("atr")
        vol_ratio = row.get("vol_ratio", 1.0)

        for val in [bb_width, bb_upper, bb_lower, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        # Check for squeeze: recent BB width was in bottom percentile
        lookback = min(p["squeeze_lookback"], idx)
        bb_widths = df.iloc[idx - lookback:idx]["bb_width"].dropna()
        if len(bb_widths) < 20:
            return None

        threshold = bb_widths.quantile(p["squeeze_percentile"] / 100)

        # Was squeezed in last few bars, now expanding
        recent_squeezed = False
        for i in range(max(0, idx - p["expansion_bars"]), idx):
            bw = df.iloc[i].get("bb_width")
            if bw is not None and not pd.isna(bw) and bw <= threshold:
                recent_squeezed = True
                break

        if not recent_squeezed:
            return None

        # Current width must be expanding (above threshold now)
        if bb_width <= threshold:
            return None

        if pd.isna(vol_ratio) or vol_ratio < p["volume_surge_ratio"]:
            return None

        # Direction: breakout above upper BB → long, below lower → short
        if close > bb_upper:
            stop = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                timestamp=current_time,
                metadata={"bb_width": bb_width, "squeeze_threshold": threshold},
            )

        if close < bb_lower:
            stop = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                timestamp=current_time,
                metadata={"bb_width": bb_width, "squeeze_threshold": threshold},
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
