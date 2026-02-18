"""Volume Profile + Order Flow strategy (Institutional-Grade).

Uses techniques from institutional/quantitative trading:
- Volume Point of Control (VPOC): Highest-volume price level acts as magnet
- Value Area (VA): 70% of volume concentrated zone = support/resistance
- Cumulative Volume Delta: Detects smart money accumulation/distribution
- Volume-weighted price absorption: Large volume with small price change = institutional activity

Entry (LONG):
  Price at/below Value Area Low + positive volume delta shift + absorption detected
  OR price returns to VPOC from below with bullish delta divergence

Entry (SHORT):
  Price at/above Value Area High + negative volume delta shift + absorption detected

Exit: VPOC target | 1.5x ATR stop | delta reversal | time stop | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class VolumeFlowStrategy(BaseStrategy):
    name = "volume_flow"

    def default_params(self) -> dict:
        return {
            "vpoc_lookback_bars": 60,       # Bars to compute volume profile (1 hour on 1m)
            "value_area_pct": 0.70,         # 70% of volume defines Value Area
            "delta_lookback": 20,           # Bars for cumulative delta calculation
            "delta_threshold": 0.55,        # Delta ratio threshold for entry
            "absorption_vol_ratio": 2.0,    # Volume surge with small price move
            "absorption_price_pct": 0.0005, # Max price change for absorption (0.05%)
            "atr_stop_mult": 1.5,
            "atr_target_mult": 2.0,
            "atr_trailing_mult": 0.8,
            "time_stop_minutes": 60,
            "eod_exit_time": "15:55",
            "min_minutes_after_open": 30,
        }

    def _compute_volume_profile(
        self, df: pd.DataFrame, idx: int, lookback: int
    ) -> Optional[dict]:
        """Compute volume profile: VPOC and Value Area from recent bars."""
        start = max(0, idx - lookback + 1)
        window = df.iloc[start:idx + 1]
        if len(window) < 10:
            return None

        prices = window["close"].values
        volumes = window["volume"].values
        price_min, price_max = float(prices.min()), float(prices.max())

        if price_max - price_min < 0.01:
            return None

        n_bins = 20
        bins = np.linspace(price_min, price_max, n_bins + 1)
        bin_centers = (bins[:-1] + bins[1:]) / 2
        bin_volumes = np.zeros(n_bins)

        for p, v in zip(prices, volumes):
            bin_idx = min(int((float(p) - price_min) / (price_max - price_min) * n_bins), n_bins - 1)
            bin_volumes[bin_idx] += float(v)

        # VPOC = price level with maximum volume
        vpoc_idx = int(np.argmax(bin_volumes))
        vpoc = float(bin_centers[vpoc_idx])

        # Value Area: expand from VPOC until 70% of total volume captured
        total_vol = float(bin_volumes.sum())
        if total_vol == 0:
            return None

        target_vol = total_vol * self.params["value_area_pct"]
        captured_vol = float(bin_volumes[vpoc_idx])
        lo, hi = vpoc_idx, vpoc_idx

        while captured_vol < target_vol and (lo > 0 or hi < n_bins - 1):
            vol_below = float(bin_volumes[lo - 1]) if lo > 0 else 0
            vol_above = float(bin_volumes[hi + 1]) if hi < n_bins - 1 else 0

            if vol_above >= vol_below and hi < n_bins - 1:
                hi += 1
                captured_vol += float(bin_volumes[hi])
            elif lo > 0:
                lo -= 1
                captured_vol += float(bin_volumes[lo])
            else:
                break

        val = float(bin_centers[lo])  # Value Area Low
        vah = float(bin_centers[hi])  # Value Area High

        return {"vpoc": vpoc, "val": val, "vah": vah, "total_volume": total_vol}

    def _compute_volume_delta(self, df: pd.DataFrame, idx: int, lookback: int) -> float:
        """Compute cumulative volume delta ratio.

        Approximation: if close > open, volume is 'buying'; otherwise 'selling'.
        Returns ratio in [-1, 1] where positive = net buying pressure.
        """
        start = max(0, idx - lookback + 1)
        window = df.iloc[start:idx + 1]
        if len(window) < 5:
            return 0.0

        buying_vol = 0.0
        selling_vol = 0.0
        for i in range(len(window)):
            bar = window.iloc[i]
            if bar["close"] >= bar["open"]:
                buying_vol += float(bar["volume"])
            else:
                selling_vol += float(bar["volume"])

        total = buying_vol + selling_vol
        if total == 0:
            return 0.0
        return (buying_vol - selling_vol) / total

    def _detect_absorption(self, df: pd.DataFrame, idx: int) -> bool:
        """Detect volume absorption: high volume with minimal price movement.

        This signals institutional activity - large orders absorbed by the market.
        """
        if idx < 3:
            return False

        row = df.iloc[idx]
        vol_ratio = row.get("vol_ratio", 1.0)
        if pd.isna(vol_ratio):
            return False

        price_change = abs(float(row["close"]) - float(row["open"])) / float(row["open"])
        return (float(vol_ratio) >= self.params["absorption_vol_ratio"]
                and price_change <= self.params["absorption_price_pct"])

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < self.params["vpoc_lookback_bars"]:
            return None

        p = self.params

        # Time filters
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t < time(10, 0) or t >= eod:
            return None

        row = df.iloc[idx]
        close = float(row["close"])
        atr = row.get("atr")
        if atr is None or pd.isna(atr) or float(atr) <= 0:
            return None
        atr = float(atr)

        # Compute volume profile
        vp = self._compute_volume_profile(df, idx, p["vpoc_lookback_bars"])
        if vp is None:
            return None

        # Compute volume delta
        delta = self._compute_volume_delta(df, idx, p["delta_lookback"])

        # Check for absorption
        absorption = self._detect_absorption(df, idx)

        vpoc = vp["vpoc"]
        val = vp["val"]
        vah = vp["vah"]

        # LONG: Price at/below Value Area Low + positive delta + absorption or strong delta
        if close <= val:
            if delta >= p["delta_threshold"] or (absorption and delta > 0):
                stop = close - p["atr_stop_mult"] * atr
                target = min(vpoc, close + p["atr_target_mult"] * atr)
                return TradeSignal(
                    strategy=self.name,
                    direction=Direction.LONG,
                    entry_price=close,
                    stop_loss=stop,
                    take_profit=target,
                    confidence=min(0.9, 0.5 + abs(delta) * 0.3 + (0.1 if absorption else 0)),
                    timestamp=current_time,
                    metadata={
                        "vpoc": round(vpoc, 2), "val": round(val, 2), "vah": round(vah, 2),
                        "delta": round(delta, 3), "absorption": absorption,
                    },
                )

        # SHORT: Price at/above Value Area High + negative delta + absorption
        if close >= vah:
            if delta <= -p["delta_threshold"] or (absorption and delta < 0):
                stop = close + p["atr_stop_mult"] * atr
                target = max(vpoc, close - p["atr_target_mult"] * atr)
                return TradeSignal(
                    strategy=self.name,
                    direction=Direction.SHORT,
                    entry_price=close,
                    stop_loss=stop,
                    take_profit=target,
                    confidence=min(0.9, 0.5 + abs(delta) * 0.3 + (0.1 if absorption else 0)),
                    timestamp=current_time,
                    metadata={
                        "vpoc": round(vpoc, 2), "val": round(val, 2), "vah": round(vah, 2),
                        "delta": round(delta, 3), "absorption": absorption,
                    },
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
        close = float(row["close"])
        atr = float(row.get("atr", 0))

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

        # Volume delta reversal: if delta flips against position, exit early
        delta = self._compute_volume_delta(df, idx, p["delta_lookback"])
        if is_long and delta < -0.4:
            return ExitSignal(reason=ExitReason.REVERSE_SIGNAL, exit_price=close, timestamp=current_time)
        if not is_long and delta > 0.4:
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

        # Time stop
        if entry_time and (current_time - entry_time).total_seconds() > p["time_stop_minutes"] * 60:
            return ExitSignal(reason=ExitReason.TIME_STOP, exit_price=close, timestamp=current_time)

        return None
