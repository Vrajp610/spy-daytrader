"""Momentum Scalper strategy.

Entry: Fast RSI(5) bounces from oversold/overbought zones with tight stops.
Regime: Trending
Exit: 1.5x ATR target | 0.75x ATR stop (tight) | trailing 0.5x ATR | 30-min time stop | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class MomentumScalperStrategy(BaseStrategy):
    name = "momentum_scalper"

    def default_params(self) -> dict:
        return {
            "rsi_fast_period": 5,
            "rsi_oversold": 25,
            "rsi_overbought": 75,
            "rsi_bounce_threshold": 5,
            "adx_min": 20,
            "atr_target_mult": 1.5,
            "atr_stop_mult": 0.75,
            "atr_trailing_mult": 0.5,
            "time_stop_minutes": 30,
            "eod_exit_time": "15:55",
        }

    def _fast_rsi(self, df: pd.DataFrame, idx: int) -> Optional[float]:
        """Compute RSI(5) at the given index using recent closes."""
        period = self.params["rsi_fast_period"]
        if idx < period + 1:
            return None
        closes = df.iloc[idx - period:idx + 1]["close"]
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 30:
            return None

        p = self.params
        row = df.iloc[idx]
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        close = row["close"]
        atr = row.get("atr")
        adx = row.get("adx")
        ema9 = row.get("ema9")

        for val in [atr, adx, ema9]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        if adx < p["adx_min"]:
            return None

        fast_rsi = self._fast_rsi(df, idx)
        prev_fast_rsi = self._fast_rsi(df, idx - 1)
        if fast_rsi is None or prev_fast_rsi is None:
            return None

        # LONG: fast RSI was oversold and is now bouncing up
        if (prev_fast_rsi <= p["rsi_oversold"]
                and fast_rsi > prev_fast_rsi + p["rsi_bounce_threshold"]
                and close > ema9):
            stop = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            rsi_bounce = abs(fast_rsi - prev_fast_rsi)
            confidence = min(0.80, 0.5 + rsi_bounce * 0.01 + max(0, (adx - 20)) * 0.005)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"fast_rsi": fast_rsi, "adx": adx},
            )

        # SHORT: fast RSI was overbought and is now dropping
        if (prev_fast_rsi >= p["rsi_overbought"]
                and fast_rsi < prev_fast_rsi - p["rsi_bounce_threshold"]
                and close < ema9):
            stop = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            rsi_bounce = abs(fast_rsi - prev_fast_rsi)
            confidence = min(0.80, 0.5 + rsi_bounce * 0.01 + max(0, (adx - 20)) * 0.005)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"fast_rsi": fast_rsi, "adx": adx},
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
