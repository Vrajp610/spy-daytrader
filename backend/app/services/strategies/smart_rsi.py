"""Smart RSI strategy — Adaptive RSI with dynamic percentile thresholds.

Inspired by the "SMART RSI" TradingView indicator (length=10, smoothing=3,
lookback=365 bars for percentile).

Standard RSI uses fixed OB/OS thresholds (70/30). Smart RSI adapts:
  - Compute RSI(10) with 3-bar smoothing
  - Over a rolling 200-bar window, find the 90th percentile (overbought)
    and 10th percentile (oversold) of RSI values
  - These dynamic thresholds adjust to the current market volatility regime

Entry:
  LONG:  smoothed RSI crosses above the 10th-percentile lower band
         (was oversold by this instrument's own standards), above VWAP,
         MACD hist positive
  SHORT: smoothed RSI crosses below the 90th-percentile upper band,
         below VWAP, MACD hist negative

Exit: 2.0x ATR target | 1.2x ATR stop | RSI crosses back to opposite band | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class SmartRSIStrategy(BaseStrategy):
    name = "smart_rsi"

    def default_params(self) -> dict:
        return {
            "rsi_len":            10,    # RSI period (shorter = more sensitive)
            "smooth_len":          3,    # EMA smoothing of RSI
            "pct_lookback":      200,    # bars for percentile calculation
            "ob_pct":             90,    # overbought percentile
            "os_pct":             10,    # oversold percentile
            "atr_target_mult":    2.0,
            "atr_stop_mult":      1.2,
            "atr_trailing_mult":  1.0,
            "eod_exit_time":     "15:55",
        }

    @staticmethod
    def _compute_smart_rsi(
        close: pd.Series,
        rsi_len: int = 10,
        smooth_len: int = 3,
    ) -> pd.Series:
        """Compute RSI(rsi_len) then smooth with EMA(smooth_len)."""
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta.clip(upper=0))
        alpha    = 1.0 / rsi_len
        avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
        avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0, np.nan)
        rsi_raw  = 100 - 100 / (1 + rs)
        # Smooth with EMA
        return rsi_raw.ewm(span=smooth_len, adjust=False).mean()

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        p = self.params
        min_bars = p["pct_lookback"] + p["rsi_len"] + p["smooth_len"] + 5
        if idx < min_bars:
            return None

        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        row  = df.iloc[idx]
        prev = df.iloc[idx - 1]

        close     = row["close"]
        macd_hist = row.get("macd_hist")
        prev_macd = prev.get("macd_hist")
        vwap      = row.get("vwap")
        atr       = row.get("atr")

        for val in [macd_hist, prev_macd, vwap, atr]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        # Compute Smart RSI over recent window
        window_close = df["close"].iloc[max(0, idx - p["pct_lookback"] - 20):idx + 1]
        smart_rsi = self._compute_smart_rsi(window_close, p["rsi_len"], p["smooth_len"])

        if len(smart_rsi) < p["pct_lookback"] + 2:
            return None
        if smart_rsi.isna().iloc[-1] or smart_rsi.isna().iloc[-2]:
            return None

        sr_now  = float(smart_rsi.iloc[-1])
        sr_prev = float(smart_rsi.iloc[-2])

        # Dynamic percentile thresholds from rolling lookback
        lookback_vals = smart_rsi.dropna().iloc[-p["pct_lookback"]:]
        if len(lookback_vals) < 50:
            return None
        ob_thresh = float(np.percentile(lookback_vals, p["ob_pct"]))
        os_thresh = float(np.percentile(lookback_vals, p["os_pct"]))

        # LONG: Smart RSI crosses above oversold threshold (exits oversold)
        if (sr_prev <= os_thresh < sr_now
                and macd_hist > 0
                and close > vwap):
            stop   = close - p["atr_stop_mult"] * atr
            target = close + p["atr_target_mult"] * atr
            depth  = max(0, os_thresh - sr_prev)  # how deep below threshold
            confidence = min(0.88, 0.54 + depth * 0.005 + (ob_thresh - os_thresh) * 0.001)
            return TradeSignal(
                strategy=self.name, direction=Direction.LONG,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"smart_rsi": round(sr_now, 1), "os_thresh": round(os_thresh, 1),
                          "ob_thresh": round(ob_thresh, 1),
                          "options_preference": "credit_spread", "suggested_dte": 7},
            )

        # SHORT: Smart RSI crosses below overbought threshold (exits overbought)
        if (sr_prev >= ob_thresh > sr_now
                and macd_hist < 0
                and close < vwap):
            stop   = close + p["atr_stop_mult"] * atr
            target = close - p["atr_target_mult"] * atr
            depth  = max(0, sr_prev - ob_thresh)
            confidence = min(0.88, 0.54 + depth * 0.005 + (ob_thresh - os_thresh) * 0.001)
            return TradeSignal(
                strategy=self.name, direction=Direction.SHORT,
                entry_price=close, stop_loss=stop, take_profit=target,
                confidence=confidence, timestamp=current_time,
                metadata={"smart_rsi": round(sr_now, 1), "os_thresh": round(os_thresh, 1),
                          "ob_thresh": round(ob_thresh, 1),
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

        # Exit when Smart RSI reaches the opposite extreme
        window_close = df["close"].iloc[max(0, idx - p["pct_lookback"] - 20):idx + 1]
        smart_rsi = self._compute_smart_rsi(window_close, p["rsi_len"], p["smooth_len"])
        if not smart_rsi.isna().iloc[-1]:
            sr_now = float(smart_rsi.iloc[-1])
            lookback_vals = smart_rsi.dropna().iloc[-p["pct_lookback"]:]
            if len(lookback_vals) >= 50:
                ob_thresh = float(np.percentile(lookback_vals, p["ob_pct"]))
                os_thresh = float(np.percentile(lookback_vals, p["os_pct"]))
                if is_long and sr_now >= ob_thresh:
                    return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)
                if not is_long and sr_now <= os_thresh:
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
