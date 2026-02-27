"""Base strategy abstract class and TradeSignal dataclass."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np

from app.services.strategies.regime_detector import MarketRegime


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_STOP = "time_stop"
    EOD = "eod"
    REVERSE_SIGNAL = "reverse_signal"
    FALSE_BREAKOUT = "false_breakout"
    SCALE_OUT_1 = "scale_out_1"
    SCALE_OUT_2 = "scale_out_2"
    ADAPTIVE_TRAILING = "adaptive_trailing"


@dataclass
class ScaleLevel:
    """Defines a scale-out level for partial position exits."""
    pct_to_close: float              # fraction of original qty to close (e.g. 0.50)
    atr_profit_multiple: float       # trigger when profit >= this * ATR
    move_stop_to_breakeven: bool     # move effective stop to entry after this scale
    new_trailing_atr_mult: Optional[float] = None  # tighten trailing to this * ATR


@dataclass
class MarketContext:
    """Bundles all timeframe data + regime info for multi-timeframe analysis."""
    df_1min: pd.DataFrame
    df_5min: pd.DataFrame
    df_15min: pd.DataFrame
    df_30min: pd.DataFrame
    df_1hr: pd.DataFrame
    df_4hr: pd.DataFrame
    regime: MarketRegime
    # Macro/options context — populated by trading engine on each loop iteration
    iv_rank: float = 50.0    # options chain IV rank (0-100 percentile)
    vix: float = 20.0        # VIX spot level


@dataclass
class TradeSignal:
    strategy: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int = 0  # calculated by risk manager
    confidence: float = 0.5
    timestamp: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ExitSignal:
    reason: ExitReason
    exit_price: float
    timestamp: Optional[datetime] = None
    quantity: Optional[int] = None  # None = close entire position


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    name: str = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params = params or self.default_params()

    @abstractmethod
    def default_params(self) -> dict:
        ...

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, idx: int, current_time: datetime, **kwargs
    ) -> Optional[TradeSignal]:
        """Check if entry conditions are met at bar index `idx`."""
        ...

    @abstractmethod
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
        """Check if exit conditions are met for an open trade."""
        ...

    @staticmethod
    def compute_confluence_score(ctx: MarketContext, direction: Direction) -> float:
        """Compute multi-timeframe confluence score (0-100).

        Evaluates trend alignment, volume confirmation, key level proximity,
        risk/reward quality, and volatility regime across all timeframes.

        Returns a score 0-100 that maps to signal confidence.
        """
        score = 0.0
        sign = 1.0 if direction == Direction.LONG else -1.0

        # ── 1. Trend alignment across timeframes (60 pts total) ──
        tf_weights = [
            (ctx.df_4hr, 25.0),
            (ctx.df_1hr, 20.0),
            (ctx.df_30min, 15.0),
            (ctx.df_15min, 12.0),
            (ctx.df_5min, 5.0),
            (ctx.df_1min, 3.0),
        ]
        for df_tf, weight in tf_weights:
            if df_tf is None or df_tf.empty or len(df_tf) < 5:
                continue
            row = df_tf.iloc[-1]
            ema9 = row.get("ema9")
            ema21 = row.get("ema21")
            if ema9 is not None and ema21 is not None and not pd.isna(ema9) and not pd.isna(ema21):
                if (ema9 > ema21 and sign > 0) or (ema9 < ema21 and sign < 0):
                    score += weight
                elif (ema9 < ema21 and sign > 0) or (ema9 > ema21 and sign < 0):
                    score -= weight * 0.5  # Penalize counter-trend

        # ── 2. Volume confirmation (10 pts) ──
        if ctx.df_1min is not None and not ctx.df_1min.empty:
            row_1m = ctx.df_1min.iloc[-1]
            vol_ratio = row_1m.get("vol_ratio", 1.0)
            if vol_ratio is not None and not pd.isna(vol_ratio):
                vol_ratio = float(vol_ratio)
                if vol_ratio >= 1.5:
                    score += 10.0
                elif vol_ratio >= 1.2:
                    score += 6.0
                elif vol_ratio >= 1.0:
                    score += 3.0

        # ── 3. Key level proximity — VWAP alignment (10 pts) ──
        if ctx.df_1min is not None and not ctx.df_1min.empty:
            row_1m = ctx.df_1min.iloc[-1]
            close = row_1m.get("close")
            vwap = row_1m.get("vwap")
            if close is not None and vwap is not None and not pd.isna(close) and not pd.isna(vwap):
                close, vwap = float(close), float(vwap)
                if (close > vwap and sign > 0) or (close < vwap and sign < 0):
                    score += 10.0
                elif (close < vwap and sign > 0) or (close > vwap and sign < 0):
                    score -= 5.0

        # ── 4. Volatility regime check (10 pts) ──
        # Avoid extremes: top 5% or bottom 5% of ATR percentile rank
        if ctx.df_1hr is not None and not ctx.df_1hr.empty and len(ctx.df_1hr) > 20:
            atr_series = ctx.df_1hr["atr"].dropna()
            if len(atr_series) > 10:
                current_atr = float(atr_series.iloc[-1])
                pct_rank = (atr_series < current_atr).sum() / len(atr_series)
                if 0.05 <= pct_rank <= 0.95:
                    score += 10.0
                elif 0.10 <= pct_rank <= 0.90:
                    score += 5.0
                # Extreme volatility: no bonus (score += 0)

        # ── 5. MACD confirmation across higher TFs (10 pts) ──
        htf_macd = [(ctx.df_1hr, 5.0), (ctx.df_4hr, 5.0)]
        for df_tf, weight in htf_macd:
            if df_tf is None or df_tf.empty:
                continue
            macd_hist = df_tf.iloc[-1].get("macd_hist")
            if macd_hist is not None and not pd.isna(macd_hist):
                if (float(macd_hist) > 0 and sign > 0) or (float(macd_hist) < 0 and sign < 0):
                    score += weight

        # ── 6. RSI momentum alignment across 1hr and 4hr (8 pts) ──
        # LONG benefits when RSI is recovering from oversold (< 50 but rising)
        # SHORT benefits when RSI is falling from overbought (> 50 but falling)
        for df_tf, weight in [(ctx.df_1hr, 5.0), (ctx.df_4hr, 3.0)]:
            if df_tf is None or df_tf.empty or len(df_tf) < 3:
                continue
            rsi_cur = df_tf.iloc[-1].get("rsi")
            rsi_prev = df_tf.iloc[-2].get("rsi")
            if rsi_cur is None or rsi_prev is None:
                continue
            if pd.isna(rsi_cur) or pd.isna(rsi_prev):
                continue
            rsi_cur, rsi_prev = float(rsi_cur), float(rsi_prev)
            rising = rsi_cur > rsi_prev
            if sign > 0 and rising and rsi_cur < 60:   # LONG: RSI rising, not overbought
                score += weight
            elif sign > 0 and rsi_cur < 40:             # LONG: deeply oversold = mean reversion
                score += weight * 0.5
            elif sign < 0 and not rising and rsi_cur > 40:  # SHORT: RSI falling, not oversold
                score += weight
            elif sign < 0 and rsi_cur > 60:             # SHORT: deeply overbought
                score += weight * 0.5

        # ── 7. ADX trend strength on 1hr (6 pts) ──
        # Strong trend (ADX > 25) confirms breakout/momentum strategies.
        # In range (ADX < 20), mean-reversion is more reliable — penalize breakouts.
        if ctx.df_1hr is not None and not ctx.df_1hr.empty:
            adx = ctx.df_1hr.iloc[-1].get("adx")
            plus_di  = ctx.df_1hr.iloc[-1].get("plus_di")
            minus_di = ctx.df_1hr.iloc[-1].get("minus_di")
            if adx is not None and not pd.isna(adx):
                adx = float(adx)
                if adx >= 25:
                    # Trend is strong — check DI alignment
                    if plus_di is not None and minus_di is not None:
                        pdi, mdi = float(plus_di), float(minus_di)
                        if (pdi > mdi and sign > 0) or (mdi > pdi and sign < 0):
                            score += 6.0   # DI aligned with direction
                        else:
                            score -= 3.0   # DI counter to direction in a strong trend
                    else:
                        score += 3.0       # Strong trend, no DI data
                elif adx < 20:
                    # Range-bound — slight bonus for mean-reversion signals
                    score += 2.0

        # ── 8. Bollinger Band position (6 pts) ──
        # LONG: price near lower band (value area), SHORT: price near upper band
        if ctx.df_1min is not None and not ctx.df_1min.empty:
            row_bb = ctx.df_1min.iloc[-1]
            close   = row_bb.get("close")
            bb_upper = row_bb.get("bb_upper")
            bb_lower = row_bb.get("bb_lower")
            if all(v is not None and not pd.isna(v) for v in [close, bb_upper, bb_lower]):
                close, bb_upper, bb_lower = float(close), float(bb_upper), float(bb_lower)
                bb_range = bb_upper - bb_lower
                if bb_range > 0:
                    bb_pct = (close - bb_lower) / bb_range   # 0 = lower band, 1 = upper band
                    if sign > 0 and bb_pct <= 0.35:          # LONG near lower band
                        score += 6.0
                    elif sign > 0 and bb_pct >= 0.65:        # LONG near upper band: overbought
                        score -= 3.0
                    elif sign < 0 and bb_pct >= 0.65:        # SHORT near upper band
                        score += 6.0
                    elif sign < 0 and bb_pct <= 0.35:        # SHORT near lower band: oversold
                        score -= 3.0

        return max(0.0, min(100.0, score))
