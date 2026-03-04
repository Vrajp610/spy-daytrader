"""VWAP Mean Reversion strategy.

Entry (LONG): Price >= 0.3% below VWAP + RSI(14) <= 30 + volume surge + 30 min after open
Exit: VWAP reversion or 1.5x ATR target | 1.0x ATR stop | trailing 0.5x ATR | 45-min time stop | EOD 3:55 PM
"""

from __future__ import annotations
from datetime import datetime, time, timedelta
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class VWAPReversionStrategy(BaseStrategy):
    name = "vwap_reversion"

    def default_params(self) -> dict:
        return {
            "vwap_deviation_pct": 0.002,  # 0.2% deviation (SPY low-ATR days rarely reach 0.3%)
            "rsi_threshold": 40,          # LONG when RSI ≤ 40 (was 30 — too extreme for daily signals)
            "rsi_short_threshold": 60,    # SHORT when RSI ≥ 60 (was 70 — nearly never reached)
            "volume_surge_ratio": 1.2,    # slightly lower: vol confirmation without over-filtering
            "min_minutes_after_open": 30,
            "atr_target_mult": 1.5,
            "atr_stop_mult": 1.0,
            "atr_trailing_mult": 0.5,
            "time_stop_minutes": 45,
            "eod_exit_time": "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 30:
            return None

        p = self.params
        row = df.iloc[idx]

        # Time filters
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        # Block 9:30-10:00 (no volume anchor) and 2:30-close (MOC imbalance distortion)
        if t < time(10, 0) or t >= time(14, 30) or t >= eod:
            return None

        close = row["close"]
        vwap = row.get("vwap")
        rsi = row.get("rsi")
        atr = row.get("atr")
        vol_ratio = row.get("vol_ratio", 1.0)
        adx = row.get("adx")

        if vwap is None or rsi is None or atr is None:
            return None
        if pd.isna(vwap) or pd.isna(rsi) or pd.isna(atr):
            return None

        # Block mean-reversion on very strong trending days — reversion fails with high conviction trends.
        # Raised from 25 to 32: SPY ADX of 25-32 often has intraday VWAP oscillations worth trading.
        if adx is not None and not pd.isna(adx) and float(adx) > 32.0:
            return None

        # LONG: price well below VWAP + oversold RSI + volume surge
        vwap_dev = (close - vwap) / vwap
        if vwap_dev <= -p["vwap_deviation_pct"] and rsi <= p["rsi_threshold"] and vol_ratio >= p["volume_surge_ratio"]:
            stop = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            confidence = min(0.9, 0.5 + abs(vwap_dev) * 50 + max(0, (30 - rsi)) * 0.005)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"vwap_dev": vwap_dev, "rsi": rsi, "options_preference": "iron_condor", "suggested_dte": 10, "suggested_delta": 0.18},
            )

        # SHORT: price well above VWAP + overbought RSI + volume surge
        if vwap_dev >= p["vwap_deviation_pct"] and rsi >= p["rsi_short_threshold"] and vol_ratio >= p["volume_surge_ratio"]:
            stop = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            confidence = min(0.9, 0.5 + abs(vwap_dev) * 50 + max(0, (rsi - 65)) * 0.005)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"vwap_dev": vwap_dev, "rsi": rsi, "options_preference": "iron_condor", "suggested_dte": 10, "suggested_delta": 0.18},
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
        vwap = row.get("vwap", close)
        atr = row.get("atr", 0)

        # EOD exit
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

        # VWAP reversion target (mean reversion complete, only if profitable)
        if is_long and close >= vwap and close > trade.entry_price:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=close, timestamp=current_time)
        if not is_long and close <= vwap and close < trade.entry_price:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=close, timestamp=current_time)

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

        # Time stop
        if entry_time and (current_time - entry_time).total_seconds() > p["time_stop_minutes"] * 60:
            return ExitSignal(reason=ExitReason.TIME_STOP, exit_price=close, timestamp=current_time)

        return None
