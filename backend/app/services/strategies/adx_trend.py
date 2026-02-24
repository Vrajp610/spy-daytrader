"""ADX Directional Trend strategy.

Entry (LONG):  ADX > 25 (strong trend) AND +DI > -DI (bullish direction)
               AND EMA9 > EMA21 AND RSI 40-65 AND above VWAP
Entry (SHORT): ADX > 25 AND -DI > +DI AND EMA9 < EMA21 AND RSI 35-60 AND below VWAP

The ADX measures trend *strength*; +DI/-DI determine *direction*.
This avoids the whipsaw problem of pure EMA crossovers in low-ADX chop.

Exit: 2.0x ATR target | 1.5x ATR stop | ADX drops below 20 | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class ADXTrendStrategy(BaseStrategy):
    name = "adx_trend"

    def default_params(self) -> dict:
        return {
            "adx_min":          25,
            "adx_exit":         18,    # exit if ADX weakens below this
            "rsi_long_min":     40,
            "rsi_long_max":     65,
            "rsi_short_min":    35,
            "rsi_short_max":    60,
            "atr_target_mult":  2.0,
            "atr_stop_mult":    1.5,
            "atr_trailing_mult":1.2,
            "eod_exit_time":    "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 30:
            return None

        p   = self.params
        row = df.iloc[idx]

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(10, 0) or t >= eod:
            return None

        close    = row["close"]
        adx      = row.get("adx")
        plus_di  = row.get("plus_di")
        minus_di = row.get("minus_di")
        ema9     = row.get("ema9")
        ema21    = row.get("ema21")
        rsi      = row.get("rsi")
        vwap     = row.get("vwap")
        atr      = row.get("atr")

        for val in [adx, plus_di, minus_di, ema9, ema21, rsi, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        if adx < p["adx_min"]:
            return None

        # LONG: +DI leading, bullish EMA alignment, above VWAP
        if (plus_di > minus_di and ema9 > ema21
                and p["rsi_long_min"] <= rsi <= p["rsi_long_max"]
                and close > vwap):
            stop   = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            di_spread = (plus_di - minus_di) / max(adx, 1)
            confidence = min(0.85, 0.50 + di_spread * 0.15 + (adx - 25) * 0.003)
            return TradeSignal(
                strategy=self.name, direction=Direction.LONG,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"adx": adx, "plus_di": plus_di, "minus_di": minus_di,
                          "options_preference": "debit_spread", "suggested_dte": 7},
            )

        # SHORT: -DI leading, bearish EMA alignment, below VWAP
        if (minus_di > plus_di and ema9 < ema21
                and p["rsi_short_min"] <= rsi <= p["rsi_short_max"]
                and close < vwap):
            stop   = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            di_spread = (minus_di - plus_di) / max(adx, 1)
            confidence = min(0.85, 0.50 + di_spread * 0.15 + (adx - 25) * 0.003)
            return TradeSignal(
                strategy=self.name, direction=Direction.SHORT,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"adx": adx, "plus_di": plus_di, "minus_di": minus_di,
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
        p   = self.params
        row = df.iloc[idx]
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

        # Exit if ADX weakens significantly (trend fading)
        adx = row.get("adx")
        if adx is not None and not pd.isna(adx) and adx < p["adx_exit"]:
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
