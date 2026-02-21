"""RSI(2) Mean Reversion strategy — Larry Connors / _CM_RSI_2_Strategy.

RSI with a 2-period length is hyper-sensitive: it hits extreme readings
(< 5 or > 95) far more frequently than RSI(14), making it ideal for
short-term mean reversion when the instrument has a dominant trend.

Rules (Connors RSI-2):
  LONG:  RSI(2) < 5 AND price is ABOVE its 200-bar EMA (in an uptrend)
         AND RSI(14) < 50 (not already overextended)
  SHORT: RSI(2) > 95 AND price is BELOW its 200-bar EMA (in a downtrend)
         AND RSI(14) > 50
  Exit: fixed ATR targets OR RSI(2) crosses 70/30 (mean achieved)

This is one of the highest-win-rate intraday strategies for large-cap ETFs.
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class RSI2MeanReversionStrategy(BaseStrategy):
    name = "rsi2_mean_reversion"

    def default_params(self) -> dict:
        return {
            "rsi2_long_threshold":   5,    # RSI(2) must be below this for LONG
            "rsi2_short_threshold":  95,   # RSI(2) must be above this for SHORT
            "rsi2_exit_long":        70,   # exit LONG when RSI(2) reaches this
            "rsi2_exit_short":       30,   # exit SHORT when RSI(2) reaches this
            "rsi14_long_max":        50,   # RSI(14) must be below for LONG
            "rsi14_short_min":       50,   # RSI(14) must be above for SHORT
            "atr_target_mult":       1.5,
            "atr_stop_mult":         1.0,
            "atr_trailing_mult":     0.8,
            "eod_exit_time":        "15:55",
        }

    @staticmethod
    def _compute_rsi2(close: pd.Series) -> pd.Series:
        """Compute RSI with period=2 inline (data_manager only stores RSI14)."""
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta.clip(upper=0))
        avg_gain = gain.ewm(alpha=0.5, adjust=False).mean()   # α=1/2 for period=2
        avg_loss = loss.ewm(alpha=0.5, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 200:
            return None

        p   = self.params
        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        row   = df.iloc[idx]
        close = row["close"]
        rsi14 = row.get("rsi")
        ema200= row.get("ema200")
        atr   = row.get("atr")
        vwap  = row.get("vwap")

        for val in [rsi14, ema200, atr, vwap]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        # Compute RSI(2) on the fly for the last 20 bars
        rsi2_series = self._compute_rsi2(df["close"].iloc[max(0, idx - 20):idx + 1])
        if len(rsi2_series) < 3:
            return None
        rsi2 = float(rsi2_series.iloc[-1])

        # LONG: deeply oversold in uptrend
        if (rsi2 < p["rsi2_long_threshold"]
                and rsi14 < p["rsi14_long_max"]
                and close > ema200):
            stop   = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            confidence = min(0.90, 0.60 + (p["rsi2_long_threshold"] - rsi2) * 0.012)
            return TradeSignal(
                strategy=self.name, direction=Direction.LONG,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"rsi2": round(rsi2, 1), "rsi14": rsi14,
                          "options_preference": "credit_spread", "suggested_dte": 5,
                          "suggested_delta": 0.25},
            )

        # SHORT: deeply overbought in downtrend
        if (rsi2 > p["rsi2_short_threshold"]
                and rsi14 > p["rsi14_short_min"]
                and close < ema200):
            stop   = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            confidence = min(0.90, 0.60 + (rsi2 - p["rsi2_short_threshold"]) * 0.012)
            return TradeSignal(
                strategy=self.name, direction=Direction.SHORT,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"rsi2": round(rsi2, 1), "rsi14": rsi14,
                          "options_preference": "credit_spread", "suggested_dte": 5,
                          "suggested_delta": 0.25},
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

        # RSI(2) mean-reversion exit
        rsi2_series = self._compute_rsi2(df["close"].iloc[max(0, idx - 20):idx + 1])
        if len(rsi2_series) >= 1:
            rsi2 = float(rsi2_series.iloc[-1])
            if is_long and rsi2 >= p["rsi2_exit_long"]:
                return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)
            if not is_long and rsi2 <= p["rsi2_exit_short"]:
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
