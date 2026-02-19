"""Gap Fill strategy.

Entry: Detects opening gap (>0.2%) and trades the fill back toward prior close.
       Only active before 10:30 AM.
Regime: Volatile
Exit: Gap fill (prior close) | 1.0x ATR stop | 60-min time stop | EOD
"""

from __future__ import annotations
from datetime import datetime, time
from typing import Optional
import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction, ExitReason,
)


class GapFillStrategy(BaseStrategy):
    name = "gap_fill"

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self._prior_closes: dict[str, float] = {}  # date_str -> prior day close

    def default_params(self) -> dict:
        return {
            "min_gap_pct": 0.002,
            "max_gap_pct": 0.015,
            "max_entry_time": "10:30",
            "atr_stop_mult": 1.0,
            "time_stop_minutes": 60,
            "eod_exit_time": "15:55",
        }

    def _get_prior_close(self, df: pd.DataFrame, idx: int, current_time: datetime) -> Optional[float]:
        """Find previous day's closing price."""
        date_str = current_time.strftime("%Y-%m-%d")
        if date_str in self._prior_closes:
            return self._prior_closes[date_str]

        current_date = current_time.date() if isinstance(current_time, datetime) else None
        if current_date is None:
            return None

        for i in range(idx - 1, max(0, idx - 500), -1):
            bar_time = df.index[i]
            bar_date = bar_time.date() if hasattr(bar_time, 'date') else None
            if bar_date is not None and bar_date < current_date:
                prior_close = float(df.iloc[i]["close"])
                self._prior_closes[date_str] = prior_close
                return prior_close

        return None

    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        if idx < 30:
            return None

        p = self.params
        row = df.iloc[idx]
        t = current_time.time() if isinstance(current_time, datetime) else current_time

        max_entry = time(*[int(x) for x in p["max_entry_time"].split(":")])
        if t < time(9, 31) or t >= max_entry:
            return None

        close = row["close"]
        atr = row.get("atr")
        if atr is None or pd.isna(atr):
            return None

        prior_close = self._get_prior_close(df, idx, current_time)
        if prior_close is None:
            return None

        # Calculate gap
        # Use today's open (first bar of the day)
        today_open = row.get("open", close)
        gap_pct = (today_open - prior_close) / prior_close

        if abs(gap_pct) < p["min_gap_pct"] or abs(gap_pct) > p["max_gap_pct"]:
            return None

        # Gap up → SHORT (expect fill down to prior close)
        if gap_pct > 0 and close > prior_close:
            stop = close + p["atr_stop_mult"] * atr
            target = prior_close
            fill_dist_ratio = abs(close - prior_close) / abs(gap_pct * prior_close) if abs(gap_pct * prior_close) > 0 else 0
            confidence = min(0.80, 0.5 + abs(gap_pct) * 30 + fill_dist_ratio * 0.1)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"gap_pct": gap_pct, "prior_close": prior_close},
            )

        # Gap down → LONG (expect fill up to prior close)
        if gap_pct < 0 and close < prior_close:
            stop = close - p["atr_stop_mult"] * atr
            target = prior_close
            fill_dist_ratio = abs(close - prior_close) / abs(gap_pct * prior_close) if abs(gap_pct * prior_close) > 0 else 0
            confidence = min(0.80, 0.5 + abs(gap_pct) * 30 + fill_dist_ratio * 0.1)
            return TradeSignal(
                strategy=self.name,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                take_profit=target,
                confidence=confidence,
                timestamp=current_time,
                metadata={"gap_pct": gap_pct, "prior_close": prior_close},
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

        t = current_time.time() if isinstance(current_time, datetime) else current_time
        eod = time(*[int(x) for x in p["eod_exit_time"].split(":")])
        if t >= eod:
            return ExitSignal(reason=ExitReason.EOD, exit_price=close, timestamp=current_time)

        is_long = trade.direction == Direction.LONG

        if is_long and close <= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)
        if not is_long and close >= trade.stop_loss:
            return ExitSignal(reason=ExitReason.STOP_LOSS, exit_price=trade.stop_loss, timestamp=current_time)

        # Gap fill target (prior close reached)
        if is_long and close >= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=close, timestamp=current_time)
        if not is_long and close <= trade.take_profit:
            return ExitSignal(reason=ExitReason.TAKE_PROFIT, exit_price=close, timestamp=current_time)

        # Time stop
        if entry_time and (current_time - entry_time).total_seconds() > p["time_stop_minutes"] * 60:
            return ExitSignal(reason=ExitReason.TIME_STOP, exit_price=close, timestamp=current_time)

        return None
