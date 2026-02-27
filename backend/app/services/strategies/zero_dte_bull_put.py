"""Zero-DTE Bull Put Spread strategy.

Strategy spec (alpaca.markets):
- Construct credit spreads by selling a put near 0.35 delta and buying a put
  2–4 strikes lower (spread width $2–$4).
- Enter only when IV rank is moderate (40–60 %).
- Exit when credit decays by 50 % or if SPY threatens the short strike.
- Allocate ≤ 1 % per trade; risk is limited to spread_width × 100 × contracts.

Implementation:
- Signal fires LONG when IV rank is 40–60%, SPY is above VWAP/EMA, and there
  is no major directional momentum in either direction (neutral to slightly bullish).
- Options selector builds the put credit spread via options_preference="force_credit_spread".
- preferred_dte=0 / min_dte=0 forces the selector to use same-day or next-day expiry.
- Entry window: 9:45 AM – 12:00 PM only (full day for time decay to work).
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class ZeroDTEBullPutStrategy(BaseStrategy):
    name = "zero_dte_bull_put"

    def default_params(self) -> dict:
        return {
            "iv_rank_min": 40.0,          # minimum IV rank (premium must be worth selling)
            "iv_rank_max": 60.0,          # maximum IV rank (avoid huge premium moves)
            "rsi_max": 65,                # avoid entering if clearly overbought
            "rsi_min": 40,                # require neutral-to-bullish bias
            "min_entry_time": "09:45",
            "max_entry_time": "12:00",   # must enter early enough for intraday theta
            "eod_exit_time": "15:50",    # exit 10 min before close on expiry day
            "spread_width": 2.0,         # $2 spread width (tight, defined risk)
        }

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 20:
            return None

        p = self.params
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        min_time = time(*[int(x) for x in p["min_entry_time"].split(":")])
        max_time = time(*[int(x) for x in p["max_entry_time"].split(":")])
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])

        if t < min_time or t >= max_time or t >= eod:
            return None

        # IV rank check — must be in moderate range for credit selling
        ctx = kwargs.get("market_context")
        iv_rank = getattr(ctx, "iv_rank", 50.0) if ctx is not None else 50.0
        if not (p["iv_rank_min"] <= iv_rank <= p["iv_rank_max"]):
            return None

        row = df.iloc[idx]
        close = row.get("close")
        vwap = row.get("vwap")
        sma20 = row.get("sma20") or row.get("ema21")
        rsi = row.get("rsi")
        atr = row.get("atr")
        ema50 = row.get("ema50")

        if any(pd.isna(v) or v is None for v in [close, rsi]):
            return None

        close, rsi = float(close), float(rsi)

        # RSI must be neutral (not overbought / oversold extremes)
        if not (p["rsi_min"] <= rsi <= p["rsi_max"]):
            return None

        # SPY must be above VWAP or EMA as directional support
        above_support = False
        if vwap is not None and not pd.isna(vwap):
            above_support = close >= float(vwap) * 0.999
        elif sma20 is not None and not pd.isna(sma20):
            above_support = close >= float(sma20)
        if not above_support:
            return None

        # Additional confirmation: EMA50 slope (positive = short-term uptrend support)
        if ema50 is not None and not pd.isna(ema50) and idx >= 5:
            ema50_prev = df.iloc[idx - 5].get("ema50")
            if ema50_prev is not None and not pd.isna(ema50_prev):
                if float(ema50) < float(ema50_prev):
                    # EMA50 is declining — not ideal for put credit spread (downside risk)
                    return None

        # Confidence: more centered IV rank = better vol regime
        iv_center_dist = abs(iv_rank - 50.0) / 10.0  # 0 at rank=50, 1 at rank=40 or 60
        confidence = min(0.80, 0.62 + (1.0 - iv_center_dist) * 0.10)

        atr_val = float(atr) if atr and not pd.isna(atr) else close * 0.004
        # Stop at 2× ATR below entry (underlying); options position limited by spread width
        stop = close - 2.5 * atr_val
        target = close + 1.5 * atr_val  # not primary; options exit at 50% credit decay

        return TradeSignal(
            strategy=self.name,
            direction=Direction.LONG,  # bullish bias (selling put spread = net long delta)
            entry_price=close,
            stop_loss=stop,
            take_profit=target,
            confidence=confidence,
            timestamp=current_time,
            metadata={
                "iv_rank": round(iv_rank, 1),
                "rsi": round(rsi, 1),
                # 0-DTE put credit spread
                "options_preference": "force_credit_spread",
                "preferred_dte": 0,
                "min_dte": 0,
                "target_delta": 0.35,   # sell 0.35 delta put (spec)
                "fallback_delta": 0.30,
                "spread_width_override": p["spread_width"],
            },
        )

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
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.EOD, exit_price=close, timestamp=current_time)

        # Underlying stop: if SPY drops through short put strike the spread is in danger
        if close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        return None
