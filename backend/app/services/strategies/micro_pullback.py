"""Micro Pullback strategy.

Entry: ADX > 30 (strong trend) + small pullback touching EMA9 → continuation trade.
Regime: Trending
Exit: 2.0x ATR target | 1.0x ATR stop | trailing 0.75x ATR | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class MicroPullbackStrategy(BaseStrategy):
    name = "micro_pullback"

    def default_params(self) -> dict:
        return {
            "adx_min": 30,
            "pullback_touch_pct": 0.001,
            "ema_fast": 9,
            "ema_slow": 21,
            "atr_target_mult": 2.0,
            "atr_stop_mult": 1.0,
            "atr_trailing_mult": 0.75,
            "eod_exit_time": "15:55",
        }

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
        low = row["low"]
        high = row["high"]
        ema9 = row.get("ema9")
        ema21 = row.get("ema21")
        adx = row.get("adx")
        atr = row.get("atr")
        rsi = row.get("rsi")

        for val in [ema9, ema21, adx, atr, rsi]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        if adx < p["adx_min"]:
            return None

        touch_dist = p["pullback_touch_pct"] * close

        # Uptrend: EMA9 > EMA21, price pulls back to touch EMA9, then closes above
        if ema9 > ema21 and close > ema9:
            # Bar's low touched or came within touch_dist of EMA9
            if low <= ema9 + touch_dist:
                # RSI not overbought (still has room)
                if rsi is not None and rsi < 70:
                    stop = close - p["atr_stop_mult"] * atr
                    target = close + p["atr_target_mult"] * atr
                    return TradeSignal(
                        strategy=self.name,
                        direction=Direction.LONG,
                        entry_price=close,
                        stop_loss=stop,
                        take_profit=target,
                        timestamp=current_time,
                        metadata={"adx": adx, "ema9": ema9, "pullback": "up"},
                    )

        # Downtrend: EMA9 < EMA21, price pulls back up to touch EMA9, then closes below
        if ema9 < ema21 and close < ema9:
            if high >= ema9 - touch_dist:
                if rsi is not None and rsi > 30:
                    stop = close + p["atr_stop_mult"] * atr
                    target = close - p["atr_target_mult"] * atr
                    return TradeSignal(
                        strategy=self.name,
                        direction=Direction.SHORT,
                        entry_price=close,
                        stop_loss=stop,
                        take_profit=target,
                        timestamp=current_time,
                        metadata={"adx": adx, "ema9": ema9, "pullback": "down"},
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
