"""Theta decay strategy — pure premium-selling via short DTE credit spreads.

No intraday technical signal required. Regime determines direction:
- TRENDING_UP / RANGE_BOUND → sell put credit spread (LONG)
- TRENDING_DOWN → sell call credit spread (SHORT)
- VOLATILE → skip (gamma blowup risk at short DTE)

The options selector handles all structure/sizing; this strategy just
provides regime-aware direction + metadata overrides.
"""

from __future__ import annotations
from datetime import datetime, date
from typing import Optional

import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction,
)
from app.services.strategies.regime_detector import MarketRegime


class ThetaDecayStrategy(BaseStrategy):
    name = "theta_decay"

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        # Rate-limit: only fire once per trading day.
        # theta_decay is a weekly premium-seller — entering the same spread
        # every bar would dominate the signal queue.  One entry per day is enough.
        self._last_signal_date: Optional[date] = None

    def default_params(self) -> dict:
        return {}

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        ctx = kwargs.get("market_context")
        regime = ctx.regime if ctx is not None else MarketRegime.RANGE_BOUND

        # Skip VOLATILE regime: short DTE + elevated gamma = unacceptable risk
        if regime == MarketRegime.VOLATILE:
            return None

        # One signal per day — don't compete with every 1-min bar
        today = current_time.date() if isinstance(current_time, datetime) else current_time
        if self._last_signal_date == today:
            return None
        self._last_signal_date = today

        close = float(df.iloc[idx]["close"]) if not df.empty else 0.0

        if regime == MarketRegime.TRENDING_UP:
            direction = Direction.LONG
            confidence = 0.72
        elif regime == MarketRegime.TRENDING_DOWN:
            direction = Direction.SHORT
            confidence = 0.72
        else:  # RANGE_BOUND
            direction = Direction.LONG
            confidence = 0.68

        return TradeSignal(
            strategy=self.name,
            direction=direction,
            entry_price=close,
            stop_loss=0.0,
            take_profit=0.0,
            confidence=confidence,
            timestamp=current_time,
            metadata={
                "options_preference": "force_credit_spread",
                "target_delta": 0.10,
                "fallback_delta": 0.20,
                "preferred_dte": 3,
                "min_dte": 2,
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
        # Options engine manages all exits for credit spreads
        return None
