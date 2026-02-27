"""Trend-Continuation Scalping strategy.

Strategy spec (quantvps.com):
- Use 5-minute charts and the 20-EMA to define the trend.
- Buy calls on pullbacks to 20-EMA in uptrends (RSI 35–45).
- Buy puts on pullbacks to 20-EMA in downtrends (RSI 55–65).
- Target +15–25 % premium; stop −10–15 %.
- Limit each trade to 1 % of equity; no more than 4 concurrent positions.
- Adjust size based on ATR and cross-sector correlations.

Implementation notes:
- Accesses 5-min DataFrame via market_context.df_5min (kwargs).
- Falls back to 1-min df with longer EMA window if 5-min unavailable.
- Uses debit spreads (delta ~0.40) for defined-risk participation.
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class TrendContinuationStrategy(BaseStrategy):
    name = "trend_continuation"

    def default_params(self) -> dict:
        return {
            "ema_period": 20,            # 20-EMA for trend definition
            "trend_ema_period": 50,      # 50-EMA for higher-TF trend confirmation
            "pullback_tolerance_pct": 0.003,  # price within 0.3% of 20-EMA
            "rsi_long_low": 35,          # RSI low bound for long pullback
            "rsi_long_high": 48,         # RSI high bound for long pullback
            "rsi_short_low": 52,         # RSI low bound for short pullback
            "rsi_short_high": 65,        # RSI high bound for short pullback
            "atr_target_mult": 1.5,      # +1.5× ATR target
            "atr_stop_mult": 0.8,        # −0.8× ATR stop (tight for scalp)
            "min_minutes_after_open": 30,
            "eod_exit_time": "15:45",    # exit early to avoid gamma risk on 0-DTE
            "max_entry_time": "14:30",   # no new entries in final 90 min
        }

    def _compute_ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _get_working_df(self, df_1min: pd.DataFrame, **kwargs) -> Optional[pd.DataFrame]:
        """Return 5-min bars if available via market_context, else None."""
        ctx = kwargs.get("market_context")
        if ctx is not None and hasattr(ctx, "df_5min"):
            df5 = ctx.df_5min
            if df5 is not None and not df5.empty and len(df5) >= 30:
                return df5
        return None

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 30:
            return None

        p = self.params
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        min_open = time(9, 30 + p["min_minutes_after_open"])
        max_entry = time(*[int(x) for x in p["max_entry_time"].split(":")])
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])

        if t < min_open or t >= max_entry or t >= eod:
            return None

        # Prefer 5-min bars for cleaner EMA; fall back to 1-min
        work_df = self._get_working_df(df, **kwargs)
        if work_df is None or len(work_df) < 25:
            # Not enough 5-min bars — skip to avoid noisy signals
            return None

        work_idx = len(work_df) - 1
        row5 = work_df.iloc[work_idx]
        close5 = float(row5["close"]) if not pd.isna(row5["close"]) else None
        if close5 is None:
            return None

        # 20-EMA and 50-EMA on 5-min bars
        close_series = work_df["close"]
        ema20 = self._compute_ema(close_series, p["ema_period"]).iloc[work_idx]
        ema50 = self._compute_ema(close_series, p["trend_ema_period"]).iloc[work_idx]

        if pd.isna(ema20) or pd.isna(ema50):
            return None

        # RSI on 5-min bars (use 'rsi' column if present, else compute)
        rsi = row5.get("rsi")
        if rsi is None or pd.isna(rsi):
            delta = close_series.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_series = 100 - 100 / (1 + rs)
            rsi = float(rsi_series.iloc[work_idx]) if not rsi_series.empty else None
        else:
            rsi = float(rsi)

        if rsi is None or pd.isna(rsi):
            return None

        # ATR for stop/target
        atr = row5.get("atr")
        if atr is None or pd.isna(atr):
            # Estimate from 1-min ATR
            atr_1m = df.iloc[idx].get("atr")
            atr = float(atr_1m) * 2.24 if atr_1m and not pd.isna(atr_1m) else 2.0
        else:
            atr = float(atr)

        tolerance = close5 * p["pullback_tolerance_pct"]
        near_ema20 = abs(close5 - ema20) <= tolerance

        # ── LONG: uptrend pullback ──────────────────────────────────────────
        # Uptrend: EMA20 > EMA50 and price is near the 20-EMA from above
        if (ema20 > ema50
                and close5 > ema20 * (1 - p["pullback_tolerance_pct"])  # slightly above or at EMA
                and near_ema20
                and p["rsi_long_low"] <= rsi <= p["rsi_long_high"]):
            stop = close5 - atr * p["atr_stop_mult"]
            target = close5 + atr * p["atr_target_mult"]
            vol_ratio = row5.get("vol_ratio", 1.0)
            vol_ratio = float(vol_ratio) if not pd.isna(vol_ratio) else 1.0
            conf = min(0.82, 0.58 + (rsi / 45) * 0.04 + min(0.08, (vol_ratio - 1.0) * 0.05))
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close5,
                stop_loss=stop,
                take_profit=target,
                confidence=conf,
                timestamp=current_time,
                metadata={
                    "ema20": round(ema20, 4),
                    "rsi": round(rsi, 1),
                    "options_preference": "debit_spread",
                    "preferred_dte": 5,
                    "target_delta": 0.40,
                    "fallback_delta": 0.35,
                },
            )

        # ── SHORT: downtrend pullback ────────────────────────────────────────
        # Downtrend: EMA20 < EMA50 and price is near the 20-EMA from below
        if (ema20 < ema50
                and close5 < ema20 * (1 + p["pullback_tolerance_pct"])
                and near_ema20
                and p["rsi_short_low"] <= rsi <= p["rsi_short_high"]):
            stop = close5 + atr * p["atr_stop_mult"]
            target = close5 - atr * p["atr_target_mult"]
            vol_ratio = row5.get("vol_ratio", 1.0)
            vol_ratio = float(vol_ratio) if not pd.isna(vol_ratio) else 1.0
            conf = min(0.82, 0.58 + ((100 - rsi) / 55) * 0.04 + min(0.08, (vol_ratio - 1.0) * 0.05))
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close5,
                stop_loss=stop,
                take_profit=target,
                confidence=conf,
                timestamp=current_time,
                metadata={
                    "ema20": round(ema20, 4),
                    "rsi": round(rsi, 1),
                    "options_preference": "debit_spread",
                    "preferred_dte": 5,
                    "target_delta": 0.40,
                    "fallback_delta": 0.35,
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

        return None
