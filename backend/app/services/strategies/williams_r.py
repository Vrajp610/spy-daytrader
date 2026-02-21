"""Williams %R Oscillator strategy.

Williams %R measures where the close is relative to the 14-bar High-Low range:
  WR = (Highest High - Close) / (Highest High - Lowest Low) × -100
Range: -100 (deeply oversold) to 0 (overbought)

Entry (LONG):  WR crosses above -80 from below (leaving oversold) + RSI < 50
               + MACD hist turning positive + above VWAP
Entry (SHORT): WR crosses below -20 from above (leaving overbought) + RSI > 50
               + MACD hist turning negative + below VWAP

Exit: 1.8x ATR target | 1.2x ATR stop | WR reaches opposite extreme | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class WilliamsRStrategy(BaseStrategy):
    name = "williams_r"

    def default_params(self) -> dict:
        return {
            "wr_oversold":      -80,   # enter LONG when WR crosses above this
            "wr_overbought":    -20,   # enter SHORT when WR crosses below this
            "wr_exit_long":     -20,   # exit LONG when WR reaches overbought
            "wr_exit_short":    -80,   # exit SHORT when WR reaches oversold
            "rsi_long_max":      50,   # RSI must be below this for LONG
            "rsi_short_min":     50,   # RSI must be above this for SHORT
            "atr_target_mult":   1.8,
            "atr_stop_mult":     1.2,
            "atr_trailing_mult": 1.0,
            "eod_exit_time":    "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 20:
            return None

        p    = self.params
        row  = df.iloc[idx]
        prev = df.iloc[idx - 1]

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        close     = row["close"]
        wr14      = row.get("wr14")
        prev_wr14 = prev.get("wr14")
        rsi       = row.get("rsi")
        macd_hist = row.get("macd_hist")
        vwap      = row.get("vwap")
        atr       = row.get("atr")

        for val in [wr14, prev_wr14, rsi, macd_hist, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        # LONG: WR crosses above -80 (exiting oversold zone)
        if (prev_wr14 <= p["wr_oversold"] < wr14
                and rsi < p["rsi_long_max"]
                and macd_hist > 0
                and close > vwap):
            stop   = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            wr_depth = abs(prev_wr14 + 80) / 20  # how deeply oversold we were
            confidence = min(0.85, 0.52 + wr_depth * 0.08 + (50 - rsi) * 0.003)
            return TradeSignal(
                strategy=self.name, direction=Direction.LONG,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"wr14": wr14, "rsi": rsi,
                          "options_preference": "credit_spread", "suggested_dte": 7},
            )

        # SHORT: WR crosses below -20 (exiting overbought zone)
        if (prev_wr14 >= p["wr_overbought"] > wr14
                and rsi > p["rsi_short_min"]
                and macd_hist < 0
                and close < vwap):
            stop   = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            wr_depth = abs(prev_wr14 + 20) / 20
            confidence = min(0.85, 0.52 + wr_depth * 0.08 + (rsi - 50) * 0.003)
            return TradeSignal(
                strategy=self.name, direction=Direction.SHORT,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"wr14": wr14, "rsi": rsi,
                          "options_preference": "credit_spread", "suggested_dte": 7},
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

        # Exit LONG when WR hits overbought (profit target area)
        # Exit SHORT when WR hits oversold
        wr14 = row.get("wr14")
        if wr14 is not None and not pd.isna(wr14):
            if is_long and wr14 >= p["wr_exit_long"]:
                return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)
            if not is_long and wr14 <= p["wr_exit_short"]:
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
