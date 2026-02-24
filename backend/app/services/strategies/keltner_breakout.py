"""Keltner Channel Breakout strategy.

Keltner Channel: EMA21 ± 1.5 × ATR14
Entry (LONG):  Close breaks *above* KC upper band + volume spike (vol_ratio > 1.3)
               + RSI 50-75 (momentum confirmation) + above VWAP
Entry (SHORT): Close breaks *below* KC lower band + vol_ratio > 1.3
               + RSI 25-50 + below VWAP

Keltner breakouts differ from Bollinger breakouts: ATR-based width means the
channel widens with volatility — breakouts only fire on genuine moves.

Exit: 2.0x ATR target | 1.5x ATR stop | close back inside channel | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class KeltnerBreakoutStrategy(BaseStrategy):
    name = "keltner_breakout"

    def default_params(self) -> dict:
        return {
            "vol_ratio_min":    1.3,
            "rsi_long_min":     50,
            "rsi_long_max":     75,
            "rsi_short_min":    25,
            "rsi_short_max":    50,
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

        close     = row["close"]
        kc_upper  = row.get("kc_upper")
        kc_lower  = row.get("kc_lower")
        vol_ratio = row.get("vol_ratio")
        rsi       = row.get("rsi")
        vwap      = row.get("vwap")
        atr       = row.get("atr")

        for val in [kc_upper, kc_lower, vol_ratio, rsi, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        if vol_ratio < p["vol_ratio_min"]:
            return None

        # Breakout above upper band → LONG
        if (close > kc_upper
                and p["rsi_long_min"] <= rsi <= p["rsi_long_max"]
                and close > vwap):
            stop   = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            confidence = min(0.87, 0.55 + (vol_ratio - 1.3) * 0.10 + (rsi - 50) * 0.002)
            return TradeSignal(
                strategy=self.name, direction=Direction.LONG,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"kc_upper": kc_upper, "vol_ratio": vol_ratio, "rsi": rsi,
                          "options_preference": "debit_spread", "suggested_dte": 7},
            )

        # Breakout below lower band → SHORT
        if (close < kc_lower
                and p["rsi_short_min"] <= rsi <= p["rsi_short_max"]
                and close < vwap):
            stop   = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            confidence = min(0.87, 0.55 + (vol_ratio - 1.3) * 0.10 + (50 - rsi) * 0.002)
            return TradeSignal(
                strategy=self.name, direction=Direction.SHORT,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"kc_lower": kc_lower, "vol_ratio": vol_ratio, "rsi": rsi,
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

        # Exit if price closes back inside the channel (failed breakout)
        kc_upper = row.get("kc_upper")
        kc_lower = row.get("kc_lower")
        if kc_upper is not None and kc_lower is not None:
            if is_long and close < kc_upper:
                return ExitSignal(ExitReason.FALSE_BREAKOUT, close, current_time)
            if not is_long and close > kc_lower:
                return ExitSignal(ExitReason.FALSE_BREAKOUT, close, current_time)

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
