"""Multi-Timeframe MA Support/Resistance (MtfMaSR) strategy.

Inspired by the MtfMaSR TradingView indicator — price respects SMA levels
(50, 100, 150, 200) as dynamic support and resistance.

Logic (adapted for 1-min intraday):
  Key MA levels: SMA50, SMA200 (proxies for the 4H/Daily MA cloud)

  LONG entry: price pulls back to within 0.15% of SMA50 or SMA200
              from above (support bounce), in an uptrend (SMA50 > SMA200),
              RSI between 40-55 (mild pullback not capitulation),
              MACD hist turning positive.

  SHORT entry: price rallies to within 0.15% of SMA50 or SMA200
               from below (resistance rejection), in a downtrend (SMA50 < SMA200),
               RSI between 45-60, MACD hist turning negative.

Exit: 1.8x ATR target | 1.2x ATR stop | price breaks through MA level | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class MtfMaSRStrategy(BaseStrategy):
    name = "mtf_ma_sr"

    def default_params(self) -> dict:
        return {
            "ma_proximity_pct":   0.15,   # % of price to consider "near" MA level
            "rsi_long_min":       38,     # RSI floor for longs (not panicking)
            "rsi_long_max":       57,     # RSI ceiling for longs (mild pullback)
            "rsi_short_min":      43,     # RSI floor for shorts (mild rally)
            "rsi_short_max":      62,     # RSI ceiling for shorts (not exhausted)
            "atr_target_mult":    1.8,
            "atr_stop_mult":      1.2,
            "atr_trailing_mult":  1.0,
            "eod_exit_time":     "15:55",
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 205:
            return None

        p   = self.params
        t   = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(9, 45) or t >= eod:
            return None

        row  = df.iloc[idx]
        prev = df.iloc[idx - 1]

        close     = row["close"]
        ema50     = row.get("ema50")
        ema200    = row.get("ema200")
        rsi       = row.get("rsi")
        macd_hist = row.get("macd_hist")
        prev_macd = prev.get("macd_hist")
        atr       = row.get("atr")
        vwap      = row.get("vwap")

        for val in [ema50, ema200, rsi, macd_hist, prev_macd, atr, vwap]:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None

        prox = p["ma_proximity_pct"] / 100.0
        in_uptrend   = ema50 > ema200
        in_downtrend = ema50 < ema200

        # Check proximity to each key MA level
        ma_levels = {"sma50": ema50, "sma200": ema200}
        for ma_name, ma_val in ma_levels.items():
            dist_pct = abs(close - ma_val) / ma_val

            if dist_pct <= prox:
                # LONG: price near MA from above in uptrend, MACD turning positive
                if (close >= ma_val                          # above the MA (support)
                        and in_uptrend
                        and p["rsi_long_min"] <= rsi <= p["rsi_long_max"]
                        and macd_hist > 0 and prev_macd <= 0):  # MACD just turned up
                    stop   = close - p["atr_stop_mult"] * atr
                    target = close + p["atr_target_mult"] * atr
                    proximity_score = (1.0 - dist_pct / prox)
                    confidence = min(0.87, 0.54 + proximity_score * 0.12 + (57 - rsi) * 0.003)
                    return TradeSignal(
                        strategy=self.name, direction=Direction.LONG,
                        entry_price=close, stop_loss=stop, take_profit=target,
                        confidence=confidence, timestamp=current_time,
                        metadata={"ma_level": ma_name, "ma_value": round(ma_val, 2),
                                  "dist_pct": round(dist_pct * 100, 3),
                                  "options_preference": "debit_spread", "suggested_dte": 7},
                    )

                # SHORT: price near MA from below in downtrend, MACD turning negative
                if (close <= ma_val                          # below the MA (resistance)
                        and in_downtrend
                        and p["rsi_short_min"] <= rsi <= p["rsi_short_max"]
                        and macd_hist < 0 and prev_macd >= 0):  # MACD just turned down
                    stop   = close + p["atr_stop_mult"] * atr
                    target = close - p["atr_target_mult"] * atr
                    proximity_score = (1.0 - dist_pct / prox)
                    confidence = min(0.87, 0.54 + proximity_score * 0.12 + (rsi - 43) * 0.003)
                    return TradeSignal(
                        strategy=self.name, direction=Direction.SHORT,
                        entry_price=close, stop_loss=stop, take_profit=target,
                        confidence=confidence, timestamp=current_time,
                        metadata={"ma_level": ma_name, "ma_value": round(ma_val, 2),
                                  "dist_pct": round(dist_pct * 100, 3),
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

        # Exit if price breaks decisively through the MA level (zone invalidated)
        entry_ma = trade.metadata.get("ma_value", 0) if trade.metadata else 0
        if entry_ma > 0:
            if is_long and close < entry_ma - atr * 0.5:
                return ExitSignal(ExitReason.REVERSE_SIGNAL, close, current_time)
            if not is_long and close > entry_ma + atr * 0.5:
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
