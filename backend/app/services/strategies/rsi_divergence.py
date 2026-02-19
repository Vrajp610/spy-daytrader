"""RSI Divergence strategy.

Entry (LONG): Price makes a new low but RSI makes a higher low → bullish divergence
Entry (SHORT): Price makes a new high but RSI makes a lower high → bearish divergence
Regime: Range-bound / Volatile
Exit: 1.5x ATR target | 1.0x ATR stop | trailing 0.75x ATR | 45-min time stop | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class RSIDivergenceStrategy(BaseStrategy):
    name = "rsi_divergence"

    def default_params(self) -> dict:
        return {
            "lookback": 20,
            "rsi_oversold": 35,
            "rsi_overbought": 65,
            "price_new_low_window": 10,
            "atr_target_mult": 1.5,
            "atr_stop_mult": 1.0,
            "atr_trailing_mult": 0.75,
            "time_stop_minutes": 45,
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
        rsi = row.get("rsi")
        atr = row.get("atr")
        if rsi is None or atr is None or pd.isna(rsi) or pd.isna(atr):
            return None

        window = p["price_new_low_window"]
        start = max(0, idx - window)

        # Bullish divergence: price new low but RSI higher low
        price_window = df.iloc[start:idx]
        if len(price_window) < 5:
            return None

        price_min_idx = price_window["close"].idxmin()
        price_min = price_window["close"].min()
        rsi_at_price_min = df.loc[price_min_idx, "rsi"] if "rsi" in df.columns else None

        if (close <= price_min * 1.002
                and rsi_at_price_min is not None
                and not pd.isna(rsi_at_price_min)
                and rsi > rsi_at_price_min
                and rsi <= p["rsi_oversold"]):
            stop = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            confidence = min(0.85, 0.5 + max(0, (35 - rsi)) * 0.005)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"rsi": rsi, "divergence": "bullish"},
            )

        # Bearish divergence: price new high but RSI lower high
        price_max_idx = price_window["close"].idxmax()
        price_max = price_window["close"].max()
        rsi_at_price_max = df.loc[price_max_idx, "rsi"] if "rsi" in df.columns else None

        if (close >= price_max * 0.998
                and rsi_at_price_max is not None
                and not pd.isna(rsi_at_price_max)
                and rsi < rsi_at_price_max
                and rsi >= p["rsi_overbought"]):
            stop = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            confidence = min(0.85, 0.5 + max(0, (rsi - 65)) * 0.005)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"rsi": rsi, "divergence": "bearish"},
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

        if entry_time and (current_time - entry_time).total_seconds() > p["time_stop_minutes"] * 60:
            return ExitSignal(reason=ExitReason.TIME_STOP, exit_price=close, timestamp=current_time)

        return None
