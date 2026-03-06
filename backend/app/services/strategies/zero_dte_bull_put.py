"""Bull Put Spread strategy — 5–10 DTE multi-day hold for theta decay income.

Strategy spec (Tastytrade / TastyLive research):
- Sell a put credit spread with 5–10 DTE (targeting weekly SPY expirations).
- Short put near 0.20 delta; buy put 2–5 strikes lower (spread width $2–$5).
- Enter when IV rank is moderate (25–70%); avoids extreme-low and panic-spike IV.
- ADX < 28 — selling premium into a strong trend is dangerous.
- Hold for theta decay; exit at 50% credit captured (2–4 days typically).
- Stop if underlying drops through the short put strike (handled by options engine).
- Allocate ≤ 1% per trade; max loss is spread_width × 100 × contracts.

Multi-day hold rationale:
- 7-DTE options carry ~$0.40–0.80 credit vs ~$0.15–0.30 for 0-DTE at same strikes.
- Theta accelerates from day 7→1, so holding 7→3 captures ~60% of total premium.
- No EOD hard exit — position stays open across sessions until 50% credit is captured.
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
            "iv_rank_min": 25.0,         # minimum IV rank; VIX=20 → rank≈33%, VIX=17.5 → rank≈25%
            "iv_rank_max": 70.0,         # cap raised: still avoid panic-spike IV but wider window
            "rsi_max": 65,               # avoid entering if clearly overbought
            "rsi_min": 35,               # require neutral-to-slightly-bullish bias
            "adx_max": 28,               # raised: 25 was too tight for intraday SPY swings
            "min_entry_time": "09:45",   # avoid first 15 min gamma explosion for weekly options
            "max_entry_time": "15:00",   # allow entries until 3 PM
            "preferred_dte": 7,          # target weekly expirations (~$0.50–0.80 credit vs $0.20 for 0-DTE)
            "min_dte": 3,                # at least 3 days left for meaningful theta
            "spread_width": 3.0,         # wider spread on weekly options = more premium
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

        if t < min_time or t >= max_time:
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
        adx = row.get("adx")

        if any(pd.isna(v) or v is None for v in [close, rsi]):
            return None

        close, rsi = float(close), float(rsi)

        # RSI must be neutral (not overbought / oversold extremes)
        if not (p["rsi_min"] <= rsi <= p["rsi_max"]):
            return None

        # Block on trending days — selling premium into strong trend is dangerous
        if adx is not None and not pd.isna(adx) and float(adx) >= p["adx_max"]:
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
                # Weekly put credit spread — hold 3-5 days for theta decay
                "options_preference": "force_credit_spread",
                "preferred_dte": p["preferred_dte"],   # 7 DTE (weekly expiration)
                "min_dte": p["min_dte"],               # 3 DTE minimum
                "target_delta": 0.20,   # 0.20 delta: ~80% win rate vs 65% at 0.35 (Tastytrade)
                "fallback_delta": 0.15,
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
        row = df.iloc[idx]
        close = float(row["close"])

        # No hard EOD exit — weekly position held across sessions until:
        #   (a) options engine closes at 50% credit captured (credit_profit_target_pct)
        #   (b) underlying falls through stop (short strike threatened)
        if close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        return None
