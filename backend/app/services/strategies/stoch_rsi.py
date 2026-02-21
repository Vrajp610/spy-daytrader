"""StochRSI strategy — RRI + StochRSI (from screenshot).

StochRSI applies the Stochastic formula to RSI(14) values:
  StochRSI = (RSI - MinRSI_N) / (MaxRSI_N - MinRSI_N)
  %K = 3-bar SMA of StochRSI × 100
  %D = 3-bar SMA of %K

Entry (LONG):  %K crosses above %D from below 20 (oversold zone)
               AND MACD hist turning positive AND above VWAP
Entry (SHORT): %K crosses below %D from above 80 (overbought zone)
               AND MACD hist turning negative AND below VWAP

StochRSI reacts much faster than RSI alone while filtering out single-bar
extremes (requires the cross confirmation), reducing false signals.

Exit: 2.0x ATR target | 1.2x ATR stop | %K crosses opposite extreme | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class StochRSIStrategy(BaseStrategy):
    name = "stoch_rsi"

    def default_params(self) -> dict:
        return {
            "rsi_len":           14,
            "stoch_len":         14,
            "smooth_k":           3,
            "smooth_d":           3,
            "ob_level":          80,   # overbought
            "os_level":          20,   # oversold
            "atr_target_mult":    2.0,
            "atr_stop_mult":      1.2,
            "atr_trailing_mult":  1.0,
            "eod_exit_time":    "15:55",
        }

    @staticmethod
    def _compute_stoch_rsi(
        rsi: pd.Series,
        stoch_len: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> tuple[pd.Series, pd.Series]:
        """Return (%K, %D) as two Series."""
        min_rsi = rsi.rolling(stoch_len).min()
        max_rsi = rsi.rolling(stoch_len).max()
        raw = (rsi - min_rsi) / (max_rsi - min_rsi).replace(0, np.nan) * 100
        k = raw.rolling(smooth_k).mean()
        d = k.rolling(smooth_d).mean()
        return k, d

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        p   = self.params
        min_bars = p["stoch_len"] + p["smooth_k"] + p["smooth_d"] + 5
        if idx < min_bars:
            return None

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        row  = df.iloc[idx]
        close     = row["close"]
        rsi_raw   = row.get("rsi")
        macd_hist = row.get("macd_hist")
        vwap      = row.get("vwap")
        atr       = row.get("atr")

        for val in [rsi_raw, macd_hist, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        # Compute StochRSI over a rolling window
        window = df["rsi"].iloc[max(0, idx - 50):idx + 1]
        if len(window) < p["stoch_len"] + p["smooth_k"] + p["smooth_d"]:
            return None

        k_series, d_series = self._compute_stoch_rsi(
            window, p["stoch_len"], p["smooth_k"], p["smooth_d"]
        )
        if k_series.isna().iloc[-1] or d_series.isna().iloc[-1]:
            return None
        if k_series.isna().iloc[-2] or d_series.isna().iloc[-2]:
            return None

        k_now  = float(k_series.iloc[-1])
        d_now  = float(d_series.iloc[-1])
        k_prev = float(k_series.iloc[-2])
        d_prev = float(d_series.iloc[-2])

        # LONG: %K crosses above %D from oversold zone
        if (k_prev <= d_prev and k_now > d_now
                and k_prev < p["ob_level"]  # was in OS zone recently
                and macd_hist > 0
                and close > vwap):
            stop   = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            confidence = min(0.86, 0.52 + (p["os_level"] - min(k_prev, d_prev)) * 0.004)
            return TradeSignal(
                strategy=self.name, direction=Direction.LONG,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"stoch_k": round(k_now, 1), "stoch_d": round(d_now, 1),
                          "options_preference": "credit_spread", "suggested_dte": 7},
            )

        # SHORT: %K crosses below %D from overbought zone
        if (k_prev >= d_prev and k_now < d_now
                and k_prev > p["os_level"]  # was in OB zone recently
                and macd_hist < 0
                and close < vwap):
            stop   = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            confidence = min(0.86, 0.52 + (max(k_prev, d_prev) - p["ob_level"]) * 0.004)
            return TradeSignal(
                strategy=self.name, direction=Direction.SHORT,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"stoch_k": round(k_now, 1), "stoch_d": round(d_now, 1),
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

        # Exit when %K reaches the opposite extreme
        window = df["rsi"].iloc[max(0, idx - 50):idx + 1]
        if len(window) >= p["stoch_len"] + p["smooth_k"] + p["smooth_d"]:
            k_series, _ = self._compute_stoch_rsi(window, p["stoch_len"], p["smooth_k"], p["smooth_d"])
            if not k_series.isna().iloc[-1]:
                k = float(k_series.iloc[-1])
                if is_long and k >= p["ob_level"]:
                    return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)
                if not is_long and k <= p["os_level"]:
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
