"""Golden Cross / Death Cross strategy.

Entry (LONG):  EMA50 crosses above EMA200 (Golden Cross) on 5-min chart
               + RSI 45-70 + ADX > 15 (some trend) + above VWAP
Entry (SHORT): EMA50 crosses below EMA200 (Death Cross)
               + RSI 30-55 + ADX > 15 + below VWAP

The 50/200 EMA crossover is one of the most widely-watched trend-change
signals. On 5-min intraday bars a single session can produce a cross when
the broader trend shifts, providing a reliable momentum entry.

Exit: 2.5x ATR target | 1.5x ATR stop | reverse cross | trailing stop | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class GoldenCrossStrategy(BaseStrategy):
    name = "golden_cross"

    def default_params(self) -> dict:
        return {
            "adx_min":          15,
            "rsi_long_min":     45,
            "rsi_long_max":     70,
            "rsi_short_min":    30,
            "rsi_short_max":    55,
            "atr_target_mult":  2.5,
            "atr_stop_mult":    1.5,
            "atr_trailing_mult":1.5,
            "eod_exit_time":    "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 200:          # need enough bars for EMA200
            return None

        p    = self.params
        row  = df.iloc[idx]
        prev = df.iloc[idx - 1]

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(10, 0) or t >= eod:
            return None

        close    = row["close"]
        ema50    = row.get("ema50")
        ema200   = row.get("ema200")
        pema50   = prev.get("ema50")
        pema200  = prev.get("ema200")
        rsi      = row.get("rsi")
        adx      = row.get("adx")
        vwap     = row.get("vwap")
        atr      = row.get("atr")

        for val in [ema50, ema200, pema50, pema200, rsi, adx, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        if adx < p["adx_min"]:
            return None

        # Golden Cross: EMA50 crosses above EMA200
        if pema50 <= pema200 and ema50 > ema200:
            if (p["rsi_long_min"] <= rsi <= p["rsi_long_max"] and close > vwap):
                stop   = close - p["atr_stop_mult"] * atr
                target = close + p["atr_target_mult"] * atr
                spread = abs(ema50 - ema200) / close
                confidence = min(0.88, 0.55 + spread * 5 + (adx - 15) * 0.004)
                return TradeSignal(
                    strategy=self.name, direction=Direction.LONG,
                    entry_price=close, stop_loss=stop, take_profit=target,
                    confidence=confidence, timestamp=current_time,
                    metadata={"ema50": ema50, "ema200": ema200, "adx": adx,
                              "options_preference": "debit_spread", "suggested_dte": 14,
                              "suggested_delta": 0.40},
                )

        # Death Cross: EMA50 crosses below EMA200
        if pema50 >= pema200 and ema50 < ema200:
            if (p["rsi_short_min"] <= rsi <= p["rsi_short_max"] and close < vwap):
                stop   = close + p["atr_stop_mult"] * atr
                target = close - p["atr_target_mult"] * atr
                spread = abs(ema50 - ema200) / close
                confidence = min(0.88, 0.55 + spread * 5 + (adx - 15) * 0.004)
                return TradeSignal(
                    strategy=self.name, direction=Direction.SHORT,
                    entry_price=close, stop_loss=stop, take_profit=target,
                    confidence=confidence, timestamp=current_time,
                    metadata={"ema50": ema50, "ema200": ema200, "adx": adx,
                              "options_preference": "debit_spread", "suggested_dte": 14,
                              "suggested_delta": 0.40},
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
        prev  = df.iloc[idx - 1] if idx > 0 else row
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

        # Reverse cross exit
        ema50  = row.get("ema50",  0)
        ema200 = row.get("ema200", 0)
        pema50 = prev.get("ema50",  0)
        pema200= prev.get("ema200", 0)
        if is_long and pema50 >= pema200 and ema50 < ema200:
            return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)
        if not is_long and pema50 <= pema200 and ema50 > ema200:
            return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)

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
