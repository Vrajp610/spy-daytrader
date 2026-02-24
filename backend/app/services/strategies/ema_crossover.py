"""EMA Crossover + RSI Filter strategy.

Entry (LONG): 9 EMA crosses above 21 EMA on 5-min chart + RSI 40-70 + MACD positive + ADX > 20 + above VWAP
Exit: 2.0x ATR target | 1.5x ATR stop | reverse EMA cross | trailing stop | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class EMACrossoverStrategy(BaseStrategy):
    name = "ema_crossover"

    def default_params(self) -> dict:
        return {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_long_min": 40,
            "rsi_long_max": 70,
            "rsi_short_min": 30,
            "rsi_short_max": 60,
            "adx_min": 20,
            "atr_target_mult": 2.0,
            "atr_stop_mult": 1.5,
            "atr_trailing_mult": 1.0,
            "eod_exit_time": "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 30:
            return None

        p = self.params
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]

        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(10, 0) or t >= eod:
            return None

        close = row["close"]
        ema9 = row.get("ema9")
        ema21 = row.get("ema21")
        prev_ema9 = prev.get("ema9")
        prev_ema21 = prev.get("ema21")
        rsi = row.get("rsi")
        macd_hist = row.get("macd_hist")
        adx = row.get("adx")
        vwap = row.get("vwap")
        atr = row.get("atr")

        # Validate indicators exist
        for val in [ema9, ema21, prev_ema9, prev_ema21, rsi, macd_hist, adx, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        # LONG: bullish EMA crossover
        if prev_ema9 <= prev_ema21 and ema9 > ema21:
            if (p["rsi_long_min"] <= rsi <= p["rsi_long_max"]
                    and macd_hist > 0
                    and adx > p["adx_min"]
                    and close > vwap):
                stop = close - p["atr_stop_mult"] * atr
                target = close + p["atr_target_mult"] * atr
                ema_spread = abs(ema9 - ema21) / close if close > 0 else 0
                confidence = min(0.85, 0.5 + ema_spread * 10 + max(0, (adx - 20)) * 0.005)
                return TradeSignal(
                    strategy=self.name,
                    direction=Direction.LONG,
                    entry_price=close,
                    stop_loss=stop,
                    take_profit=target,
                    confidence=confidence,
                    timestamp=current_time,
                    metadata={"ema9": ema9, "ema21": ema21, "rsi": rsi, "adx": adx, "options_preference": "credit_spread", "suggested_dte": 10, "suggested_delta": 0.20},
                )

        # SHORT: bearish EMA crossover
        if prev_ema9 >= prev_ema21 and ema9 < ema21:
            if (p["rsi_short_min"] <= rsi <= p["rsi_short_max"]
                    and macd_hist < 0
                    and adx > p["adx_min"]
                    and close < vwap):
                stop = close + p["atr_stop_mult"] * atr
                target = close - p["atr_target_mult"] * atr
                ema_spread = abs(ema9 - ema21) / close if close > 0 else 0
                confidence = min(0.85, 0.5 + ema_spread * 10 + max(0, (adx - 20)) * 0.005)
                return TradeSignal(
                    strategy=self.name,
                    direction=Direction.SHORT,
                    entry_price=close,
                    stop_loss=stop,
                    take_profit=target,
                    confidence=confidence,
                    timestamp=current_time,
                    metadata={"ema9": ema9, "ema21": ema21, "rsi": rsi, "adx": adx, "options_preference": "credit_spread", "suggested_dte": 10, "suggested_delta": 0.20},
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
        prev = df.iloc[idx - 1] if idx > 0 else row
        close = row["close"]
        atr = row.get("atr", 0)

        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.EOD, exit_price=close, timestamp=current_time)

        is_long = trade.direction == Direction.LONG

        # Stop loss
        if is_long and close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        # Take profit
        if is_long and close >= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)
        if not is_long and close <= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=trade.take_profit, timestamp=current_time)

        # Reverse EMA cross
        ema9 = row.get("ema9", 0)
        ema21 = row.get("ema21", 0)
        prev_ema9 = prev.get("ema9", 0)
        prev_ema21 = prev.get("ema21", 0)
        if is_long and prev_ema9 >= prev_ema21 and ema9 < ema21:
            return ExitSignal(reason=ExitReason.REVERSE_SIGNAL, exit_price=close, timestamp=current_time)
        if not is_long and prev_ema9 <= prev_ema21 and ema9 > ema21:
            return ExitSignal(reason=ExitReason.REVERSE_SIGNAL, exit_price=close, timestamp=current_time)

        # Trailing stop
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
