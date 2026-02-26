"""Long-term backtester using daily OHLCV bars (10-15 year horizon)."""

from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from app.services.historical_data import HistoricalDataManager

logger = logging.getLogger(__name__)

# All 21 strategy names — original 12 + 4 technical (Feb 2026) + 5 from screenshots
ALL_STRATEGIES = [
    # Original 12
    "vwap_reversion", "orb", "ema_crossover", "volume_flow", "mtf_momentum",
    "rsi_divergence", "bb_squeeze", "macd_reversal", "momentum_scalper",
    "gap_fill", "micro_pullback", "double_bottom_top",
    # Technical additions (Feb 2026)
    "adx_trend", "golden_cross", "keltner_breakout", "williams_r",
    # From user's TradingView screenshots
    "rsi2_mean_reversion", "stoch_rsi", "smc_supply_demand", "mtf_ma_sr", "smart_rsi",
    # ICT Smart Money Concepts (5-confluence system)
    "smc_ict",
    # Credit-spread / LT-only strategies (no 1-min intraday model)
    "theta_decay",
]

# Strategies that exist only in the LT daily backtester.
# composite_score = lt_composite directly (no 55/45 ST/LT blend since there is no ST model).
LT_ONLY_STRATEGIES: frozenset = frozenset({"theta_decay"})

# Slippage / commission constants
SLIPPAGE_BPS = 1          # 1 basis point per side
COMMISSION_PER_SHARE = 0.005  # $0.005 round-trip (already combined)


@dataclass
class DailyTrade:
    date: str
    strategy: str
    direction: str        # LONG / SHORT
    entry: float
    exit: float
    shares: int
    pnl: float
    pnl_pct: float
    exit_reason: str      # stop / target / eod
    capital_before: float
    capital_after: float


@dataclass
class LongTermResult:
    # Extended metrics
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    total_return_pct: float
    win_rate: float
    total_trades: int
    profit_factor: float
    avg_win: float
    avg_loss: float
    final_capital: float
    years_tested: float
    equity_curve: list[dict]
    yearly_returns: list[dict]
    trades: list[dict]


# ── Daily signal generators ───────────────────────────────────────────────────

def _sig_vwap_reversion(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Price deviation from SMA20: >2% above → SHORT, <2% below → LONG."""
    if pd.isna(row.sma20) or row.sma20 == 0:
        return None
    dev = (row.close - row.sma20) / row.sma20 * 100
    if dev < -2.0:
        return "LONG"
    if dev > 2.0:
        return "SHORT"
    return None


def _sig_orb(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Breakout above/below previous day's high/low."""
    if pd.isna(prev.high) or pd.isna(prev.low):
        return None
    if row.close > prev.high:
        return "LONG"
    if row.close < prev.low:
        return "SHORT"
    return None


def _sig_ema_crossover(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """EMA9/21 crossover filtered by ADX > 20."""
    if any(pd.isna([row.ema9, row.ema21, prev.ema9, prev.ema21, row.adx14])):
        return None
    if row.adx14 < 20:
        return None
    # Bullish crossover
    if prev.ema9 <= prev.ema21 and row.ema9 > row.ema21:
        return "LONG"
    # Bearish crossover
    if prev.ema9 >= prev.ema21 and row.ema9 < row.ema21:
        return "SHORT"
    return None


def _sig_volume_flow(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """High-volume directional day: vol_ratio > 1.5."""
    if pd.isna(row.vol_ratio) or row.vol_ratio <= 1.5:
        return None
    if row.close > row.open:
        return "LONG"
    if row.close < row.open:
        return "SHORT"
    return None


def _sig_mtf_momentum(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """ROC5 + ROC20 both same direction."""
    if any(pd.isna([row.roc5, row.roc20])):
        return None
    if row.roc5 > 0 and row.roc20 > 0:
        return "LONG"
    if row.roc5 < 0 and row.roc20 < 0:
        return "SHORT"
    return None


def _sig_rsi_divergence(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """RSI < 35 → LONG, RSI > 65 → SHORT."""
    if pd.isna(row.rsi14):
        return None
    if row.rsi14 < 35:
        return "LONG"
    if row.rsi14 > 65:
        return "SHORT"
    return None


def _sig_bb_squeeze(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """BB width squeeze then breakout above upper / below lower."""
    if any(pd.isna([row.bb_width, prev.bb_width, row.bb_upper, row.bb_lower])):
        return None
    # Squeeze: current width narrower than previous
    squeezed = row.bb_width < prev.bb_width * 0.95
    if not squeezed:
        return None
    if row.close > row.bb_upper:
        return "LONG"
    if row.close < row.bb_lower:
        return "SHORT"
    return None


def _sig_macd_reversal(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """MACD histogram crossover (zero crossing)."""
    if any(pd.isna([row.macd_hist, prev.macd_hist])):
        return None
    if prev.macd_hist <= 0 and row.macd_hist > 0:
        return "LONG"
    if prev.macd_hist >= 0 and row.macd_hist < 0:
        return "SHORT"
    return None


def _sig_momentum_scalper(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Strong ROC5 > 1.5% or < -1.5%."""
    if pd.isna(row.roc5):
        return None
    if row.roc5 > 1.5:
        return "LONG"
    if row.roc5 < -1.5:
        return "SHORT"
    return None


def _sig_gap_fill(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Gap > 0.8%, fade toward prior close (mean reversion)."""
    if pd.isna(row.gap_pct):
        return None
    # Gap up → SHORT (expect fill), gap down → LONG (expect fill)
    if row.gap_pct > 0.8:
        return "SHORT"
    if row.gap_pct < -0.8:
        return "LONG"
    return None


def _sig_micro_pullback(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Pullback in uptrend: close < EMA21 but EMA21 > EMA50."""
    if any(pd.isna([row.close, row.ema21, row.ema50])):
        return None
    if row.ema21 > row.ema50 and row.close < row.ema21:
        return "LONG"
    if row.ema21 < row.ema50 and row.close > row.ema21:
        return "SHORT"
    return None


def _sig_double_bottom_top(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """RSI divergence: price making new low while RSI not (double bottom → LONG), vice versa."""
    if any(pd.isna([row.rsi14, prev.rsi14, row.close, prev.close])):
        return None
    # Double bottom: price lower but RSI higher (bullish divergence)
    if row.close < prev.close and row.rsi14 > prev.rsi14 and row.rsi14 < 45:
        return "LONG"
    # Double top: price higher but RSI lower (bearish divergence)
    if row.close > prev.close and row.rsi14 < prev.rsi14 and row.rsi14 > 55:
        return "SHORT"
    return None


def _sig_adx_trend(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """ADX > 25 with +DI/-DI directional alignment + EMA9/21 confirmation."""
    for col in ["adx14", "ema9", "ema21"]:
        if pd.isna(row.get(col)):
            return None
    if row.adx14 < 25:
        return None
    # Use EMA slope as a proxy for +DI/-DI direction (we have both in daily indicators)
    if row.ema9 > row.ema21 and row.close > row.ema9:
        return "LONG"
    if row.ema9 < row.ema21 and row.close < row.ema9:
        return "SHORT"
    return None


def _sig_golden_cross(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """EMA50 / EMA200 golden / death cross on daily bars."""
    for col in ["ema50", "ema200"]:
        if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
            return None
    if prev.ema50 <= prev.ema200 and row.ema50 > row.ema200:
        return "LONG"
    if prev.ema50 >= prev.ema200 and row.ema50 < row.ema200:
        return "SHORT"
    return None


def _sig_keltner_breakout(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Close breaks Keltner Channel (EMA21 ± 1.5×ATR) with above-average volume."""
    if any(pd.isna([row.get("ema21"), row.get("atr14"), row.get("vol_ratio")])):
        return None
    if row.vol_ratio < 1.2:
        return None
    kc_upper = row.ema21 + 1.5 * row.atr14
    kc_lower = row.ema21 - 1.5 * row.atr14
    if row.close > kc_upper:
        return "LONG"
    if row.close < kc_lower:
        return "SHORT"
    return None


def _sig_williams_r(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Williams %R(14) exits oversold/overbought zone with MACD confirmation."""
    for col in ["rsi14", "macd_hist"]:
        if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
            return None
    # Approximate WR from RSI: RSI < 20 ≈ WR < -80; RSI > 80 ≈ WR > -20
    # (True WR is in historical_data; this adapts for LT bars without adding a new col)
    if row.rsi14 < 20 and prev.rsi14 < 20 and row.rsi14 > prev.rsi14 and row.macd_hist > prev.macd_hist:
        return "LONG"
    if row.rsi14 > 80 and prev.rsi14 > 80 and row.rsi14 < prev.rsi14 and row.macd_hist < prev.macd_hist:
        return "SHORT"
    return None


def _sig_rsi2(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """RSI(2) extreme mean reversion (Larry Connors).
    RSI(2) < 5 in uptrend (close > SMA20) → LONG
    RSI(2) > 95 in downtrend (close < SMA20) → SHORT
    RSI(2) is computed from the standard RSI14 delta approximation using a 2-period window.
    """
    # Approximate RSI(2) from rsi14 by looking at 2-bar momentum
    if any(pd.isna([row.get("rsi14"), prev.get("rsi14"), row.get("sma20")])):
        return None
    # RSI(2) is much more volatile — when rsi14 is very low it's also extreme on rsi2
    # We use rsi14 < 15 as proxy for rsi2 < 5 (empirically close for SPY)
    if row.rsi14 < 15 and row.close > row.sma20:   # oversold within uptrend
        return "LONG"
    if row.rsi14 > 85 and row.close < row.sma20:   # overbought within downtrend
        return "SHORT"
    return None


def _sig_stoch_rsi(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """StochRSI: RSI < 30 turning up → LONG, RSI > 70 turning down → SHORT.
    (True StochRSI normalises RSI over a window; we use RSI momentum as proxy.)
    """
    for col in ["rsi14"]:
        if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
            return None
    # Cross out of oversold: RSI was < 30, now rising above 30
    if prev.rsi14 < 30 and row.rsi14 >= 30 and row.rsi14 > prev.rsi14:
        return "LONG"
    # Cross out of overbought: RSI was > 70, now falling below 70
    if prev.rsi14 > 70 and row.rsi14 <= 70 and row.rsi14 < prev.rsi14:
        return "SHORT"
    return None


def _sig_smc_supply_demand(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Smart Money Concepts: supply/demand zone retest.
    Demand: price dipped below SMA20 band (supply/demand zone) then recovered → LONG
    Supply: price spiked above SMA20 band then rejected → SHORT
    Uses BB bands as proxy for supply/demand zones (price outside 1-std band).
    """
    for col in ["bb_mid", "bb_std", "rsi14"]:
        if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
            return None
    zone_upper = row.bb_mid + row.bb_std       # 1-std supply zone top
    zone_lower = row.bb_mid - row.bb_std       # 1-std demand zone bottom
    # Demand zone retest: yesterday dipped into zone, today recovering
    if prev.close < zone_lower and row.close > prev.close and row.rsi14 < 50:
        return "LONG"
    # Supply zone retest: yesterday spiked into zone, today rejecting
    if prev.close > zone_upper and row.close < prev.close and row.rsi14 > 50:
        return "SHORT"
    return None


def _sig_mtf_ma_sr(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Multi-timeframe MA Support/Resistance bounce.
    Price pulls back to a major SMA (50/200) then reverses with RSI confirmation.
    """
    for col in ["ema50", "ema200", "rsi14", "atr14"]:
        if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
            return None
    atr = row.atr14
    # Bounce off EMA50 in uptrend (close > EMA200, pull back to EMA50 zone)
    if (row.close > row.ema200
            and abs(row.close - row.ema50) < 0.5 * atr
            and row.close > prev.close
            and 35 < row.rsi14 < 60):
        return "LONG"
    # Rejection at EMA50 in downtrend (close < EMA200, rally to EMA50 zone)
    if (row.close < row.ema200
            and abs(row.close - row.ema50) < 0.5 * atr
            and row.close < prev.close
            and 40 < row.rsi14 < 65):
        return "SHORT"
    return None


def _sig_smart_rsi(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """Smart RSI: adaptive RSI(10) on HL2, compare against rolling percentile bands.
    Entry when RSI(10) drops below the 10th percentile (adaptive oversold) → LONG
    or rises above 90th percentile (adaptive overbought) → SHORT.
    Approximated using rsi14 and rolling context from SMA20.
    """
    for col in ["rsi14", "sma20", "bb_std"]:
        if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
            return None
    # Use BB-width-normalised RSI as adaptive threshold
    # Wide BB (high vol) → thresholds shift inward (easier to trigger)
    bb_width_factor = row.bb_std / max(row.sma20 * 0.005, 0.001)  # normalise by ~0.5% of price
    adaptive_low = max(20, 35 - bb_width_factor * 5)
    adaptive_high = min(80, 65 + bb_width_factor * 5)

    if row.rsi14 < adaptive_low and prev.rsi14 < adaptive_low and row.close > row.sma20:
        return "LONG"
    if row.rsi14 > adaptive_high and prev.rsi14 > adaptive_high and row.close < row.sma20:
        return "SHORT"
    return None


def _sig_theta_decay(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """
    Theta decay credit-spread entry signal.

    Fires when the environment is favourable for premium selling:
      - Moderate volatility (ATR/price 0.4%–1.5%): enough premium to collect,
        not so much that gamma risk overwhelms (proxy for VIX 8–24).
      - RSI14 in the neutral zone 35–65: no runaway momentum in either direction.
      - Today's bar range < 1.5 × ATR14: no outsized momentum candle.
      - Clear EMA trend for direction:
          LONG  (sell put spread) : close > ema50 AND close > ema200
          SHORT (sell call spread): close < ema50 AND close < ema200

    Returns "LONG", "SHORT", or None.
    """
    for col in ("atr14", "rsi14", "ema50", "ema200"):
        if pd.isna(row.get(col)):
            return None

    atr   = float(row.atr14)
    close = float(row.close)
    if close <= 0 or atr <= 0:
        return None

    atr_pct = atr / close * 100          # expressed as a percentage, e.g. 0.8

    # Moderate volatility gate: skip both too-quiet and too-wild days
    if not (0.4 <= atr_pct <= 1.5):
        return None

    # RSI neutral zone: avoid entering near momentum extremes
    if not (35 <= float(row.rsi14) <= 65):
        return None

    # No large-range candles (momentum continuation risk)
    day_range = float(row.high) - float(row.low)
    if day_range >= 1.5 * atr:
        return None

    ema50  = float(row.ema50)
    ema200 = float(row.ema200)

    # Sell put credit spread in clear uptrend
    if close > ema50 and close > ema200:
        return "LONG"
    # Sell call credit spread in clear downtrend
    if close < ema50 and close < ema200:
        return "SHORT"
    return None


def _sig_smc_ict(row: pd.Series, prev: pd.Series) -> Optional[str]:
    """
    Daily-bar ICT Smart Money Concepts signal — 5-confluence scoring system.

    Each confluence votes LONG or SHORT; signal fires when ≥3 agree.

    Confluence 1 — HTF Order Block (last opposing daily candle before 3-day impulse)
    Confluence 2 — Fair Value Gap       (3-day gap with price returning to fill)
    Confluence 3 — Liquidity Sweep      (daily wick through N-day H/L, closes back inside)
    Confluence 4 — RSI divergence proxy (price vs RSI swing – approximates SMT)
    Confluence 5 — External liquidity   (price approaching Equal Highs or Equal Lows)

    Uses the `_df` context injected via closure; falls back gracefully when
    columns are missing.
    """
    required = ("close", "high", "low", "open", "atr14", "rsi14", "ema50", "ema200")
    for col in required:
        if pd.isna(row.get(col)):
            return None

    close  = float(row.close)
    high   = float(row.high)
    low    = float(row.low)
    atr    = float(row.atr14)
    rsi    = float(row.rsi14)
    ema50  = float(row.ema50)
    ema200 = float(row.ema200)

    score_long  = 0
    score_short = 0

    # ── C1: Order Block ───────────────────────────────────────────────────────
    # Provided by the `prev` series: last opposing candle before current impulse.
    prev_bear = float(prev.get("close", close)) < float(prev.get("open", close))
    prev_bull = float(prev.get("close", close)) > float(prev.get("open", close))
    this_bull = close > float(row.open)
    this_bear = close < float(row.open)

    if prev_bear and this_bull:
        # Potential bullish OB test: today's close back inside yesterday's body
        ob_high = float(prev.get("open", close))
        ob_low  = float(prev.get("close", close))
        if ob_low * 0.998 <= close <= ob_high * 1.002:
            score_long += 1
    if prev_bull and this_bear:
        ob_high = float(prev.get("close", close))
        ob_low  = float(prev.get("open", close))
        if ob_low * 0.998 <= close <= ob_high * 1.002:
            score_short += 1

    # ── C2: Fair Value Gap (3-day gap with current bar returning to it) ───────
    # prev_prev handled via the prev.name index offset below (best-effort)
    # Approximate: ATR-scaled gap on the previous two bars
    prev_high = float(prev.get("high", high))
    prev_low  = float(prev.get("low", low))
    # Bullish FVG proxy: prev bar low > recent close range → gap above
    gap_up_exists   = prev_low > close * 1.002
    gap_down_exists = prev_high < close * 0.998
    if gap_up_exists and this_bull:
        score_long += 1
    if gap_down_exists and this_bear:
        score_short += 1

    # ── C3: Liquidity Sweep ───────────────────────────────────────────────────
    # Day's wick pierced N-day H/L but closed back inside → stop-hunt reversal
    # (We approximate using ATR: a wick > 1.0× ATR beyond prior close)
    prev_close = float(prev.get("close", close))
    wick_low   = prev_close - low   # how far low went below prev close
    wick_high  = high - prev_close  # how far high went above prev close

    if wick_low > atr * 0.8 and close > prev_close * 0.998:
        score_long += 1   # bullish sweep: wick below, closed back up
    if wick_high > atr * 0.8 and close < prev_close * 1.002:
        score_short += 1  # bearish sweep: wick above, closed back down

    # ── C4: RSI Divergence proxy (≈ SMT between price and momentum) ──────────
    prev_rsi   = float(prev.get("rsi14", rsi))
    prev_close_val = float(prev.get("close", close))

    # Bullish: price makes lower close but RSI is higher → momentum not confirming
    if close < prev_close_val * 0.999 and rsi > prev_rsi * 1.03:
        score_long += 1
    # Bearish: price makes higher close but RSI is lower
    if close > prev_close_val * 1.001 and rsi < prev_rsi * 0.97:
        score_short += 1

    # ── C5: External Liquidity (EQH / EQL approximation) ─────────────────────
    # Price is near but below a significant resistance (EQH) → targeting up
    # Price is near but above a significant support (EQL) → targeting down
    atr_pct = atr / close
    # Near EQH: price within 1× ATR below the ema200/ema50 rejection zone
    # Near EQL: price within 1× ATR above the ema200/ema50 support zone
    dist_above_ema50  = (close - ema50)  / close
    dist_below_ema50  = (ema50 - close)  / close

    if 0 < dist_above_ema50 < atr_pct * 2:
        # Price just crossed above EMA50 — targeting next liquidity above
        score_long += 1
    if 0 < dist_below_ema50 < atr_pct * 2:
        # Price just crossed below EMA50 — targeting next liquidity below
        score_short += 1

    # ── Verdict ───────────────────────────────────────────────────────────────
    if score_long >= 3 and score_long > score_short:
        return "LONG"
    if score_short >= 3 and score_short > score_long:
        return "SHORT"
    return None


_SIGNAL_FUNCS = {
    "vwap_reversion":    _sig_vwap_reversion,
    "orb":               _sig_orb,
    "ema_crossover":     _sig_ema_crossover,
    "volume_flow":       _sig_volume_flow,
    "mtf_momentum":      _sig_mtf_momentum,
    "rsi_divergence":    _sig_rsi_divergence,
    "bb_squeeze":        _sig_bb_squeeze,
    "macd_reversal":     _sig_macd_reversal,
    "momentum_scalper":  _sig_momentum_scalper,
    "gap_fill":          _sig_gap_fill,
    "micro_pullback":    _sig_micro_pullback,
    "double_bottom_top": _sig_double_bottom_top,
    # New (Feb 2026)
    "adx_trend":         _sig_adx_trend,
    "golden_cross":      _sig_golden_cross,
    "keltner_breakout":  _sig_keltner_breakout,
    "williams_r":        _sig_williams_r,
    # From screenshots (Feb 2026)
    "rsi2_mean_reversion":  _sig_rsi2,
    "stoch_rsi":            _sig_stoch_rsi,
    "smc_supply_demand":    _sig_smc_supply_demand,
    "mtf_ma_sr":            _sig_mtf_ma_sr,
    "smart_rsi":            _sig_smart_rsi,
    # ICT SMC (Feb 2026)
    "smc_ict":              _sig_smc_ict,
    # Credit-spread / LT-only (Feb 2026)
    "theta_decay":          _sig_theta_decay,
}


# ── Trade simulation ──────────────────────────────────────────────────────────

def simulate_day_trade(
    direction: str,
    entry: float,
    high: float,
    low: float,
    close: float,
    stop: float,
    target: float,
) -> tuple[float, str]:
    """
    Simulate one day-trade given entry at open, OHLCV data, stop and target.

    Returns (exit_price, exit_reason).

    Stop/target disambiguation: if both could have been hit, whichever is
    closer to entry was likely hit first.
    """
    stop_hit   = (direction == "LONG"  and low  <= stop)   or (direction == "SHORT" and high >= stop)
    target_hit = (direction == "LONG"  and high >= target) or (direction == "SHORT" and low  <= target)

    if stop_hit and target_hit:
        stop_dist   = abs(entry - stop)
        target_dist = abs(entry - target)
        if stop_dist <= target_dist:
            return stop, "stop"
        else:
            return target, "target"
    elif target_hit:
        return target, "target"
    elif stop_hit:
        return stop, "stop"
    else:
        return close, "eod"


def simulate_credit_spread(
    direction: str,
    entry: float,
    expiry_close: float,
    atr: float,
    spread_width: float = 5.0,
    credit_pct: float = 0.10,
) -> tuple:
    """
    Compute the P&L for one credit spread held to expiry (3 daily bars).

    direction : "LONG"  = put credit spread (profit when price stays HIGH)
                "SHORT" = call credit spread (profit when price stays LOW)

    Geometry
    --------
    short_dist  = 1.5 × ATR  (proxy for ~0.10–0.15 delta strike at 3 DTE on SPY)
    credit      = spread_width × credit_pct       (e.g. $0.50 on a $5 spread)
    max_profit  = credit × 100                    (per contract)
    max_loss    = (spread_width - credit) × 100   (per contract)

    Payoff (LONG / put spread)
    --------------------------
    expiry_close ≥ short_strike              → full max_profit
    expiry_close ≤ short_strike - spread_width → full max_loss
    in-between                               → linear interpolation

    Returns (pnl_dollars_per_contract: float, outcome: str)
    outcome: "full_profit" | "full_loss" | "partial"
    """
    credit     = spread_width * credit_pct
    max_profit = credit * 100
    max_loss   = (spread_width - credit) * 100
    short_dist = 1.5 * atr

    if direction == "LONG":
        short_strike = entry - short_dist
        long_strike  = short_strike - spread_width   # lower protective put
        if expiry_close >= short_strike:
            return max_profit, "full_profit"
        elif expiry_close <= long_strike:
            return -max_loss, "full_loss"
        else:
            # 0 at long_strike → max_profit at short_strike (linear)
            frac = (expiry_close - long_strike) / spread_width
            pnl  = -max_loss + frac * (max_profit + max_loss)
            return round(pnl, 2), "partial"
    else:  # SHORT / call credit spread
        short_strike = entry + short_dist
        long_strike  = short_strike + spread_width   # higher protective call
        if expiry_close <= short_strike:
            return max_profit, "full_profit"
        elif expiry_close >= long_strike:
            return -max_loss, "full_loss"
        else:
            frac = (long_strike - expiry_close) / spread_width
            pnl  = -max_loss + frac * (max_profit + max_loss)
            return round(pnl, 2), "partial"


# ── Position sizing ───────────────────────────────────────────────────────────

def _size_position(
    capital: float,
    entry: float,
    stop: float,
    max_risk_per_trade: float,
    max_position_pct: float,
) -> int:
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return 0
    risk_amount = capital * max_risk_per_trade
    shares_by_risk = int(risk_amount / stop_distance)
    cap_shares = int(max_position_pct * capital / entry)
    return max(0, min(shares_by_risk, cap_shares))


# ── Metrics computation ───────────────────────────────────────────────────────

def _compute_extended_metrics(
    trades: list[DailyTrade],
    equity_curve: list[tuple[str, float]],
    initial_capital: float,
    years_tested: float,
) -> dict:
    if not trades:
        return {
            "cagr_pct": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
            "calmar_ratio": 0.0, "max_drawdown_pct": 0.0,
            "total_return_pct": 0.0, "win_rate": 0.0, "total_trades": 0,
            "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        }

    final_capital = equity_curve[-1][1]
    total_return  = (final_capital - initial_capital) / initial_capital * 100

    # CAGR
    if years_tested > 0:
        cagr = ((final_capital / initial_capital) ** (1 / years_tested) - 1) * 100
    else:
        cagr = 0.0

    # Daily returns from equity curve
    eq_series = pd.Series([v for _, v in equity_curve])
    daily_ret = eq_series.pct_change().dropna()

    rf_daily = 0.0  # 0% risk-free — Sharpe used for relative strategy ranking, not absolute T-bill comparison

    # Sharpe
    excess = daily_ret - rf_daily
    sharpe = (excess.mean() / excess.std(ddof=1) * math.sqrt(252)) if excess.std(ddof=1) > 0 else 0.0

    # Sortino
    downside = daily_ret[daily_ret < rf_daily] - rf_daily
    downside_std = math.sqrt((downside ** 2).mean()) if len(downside) > 0 else 0.0
    sortino = (excess.mean() * math.sqrt(252) / downside_std) if downside_std > 0 else 0.0

    # Max drawdown
    roll_max = eq_series.cummax()
    dd = (eq_series - roll_max) / roll_max * 100
    max_dd = dd.min()  # negative number

    # Calmar
    calmar = (cagr / abs(max_dd)) if abs(max_dd) > 0 else 0.0

    # Win / loss
    wins  = [t.pnl for t in trades if t.pnl > 0]
    losses= [t.pnl for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    avg_win  = np.mean(wins)  if wins  else 0.0
    avg_loss = np.mean(losses)if losses else 0.0

    return {
        "cagr_pct":        round(cagr, 4),
        "sharpe_ratio":    round(sharpe, 4),
        "sortino_ratio":   round(sortino, 4),
        "calmar_ratio":    round(calmar, 4),
        "max_drawdown_pct":round(abs(max_dd), 4),
        "total_return_pct":round(total_return, 4),
        "win_rate":        round(win_rate, 4),
        "total_trades":    len(trades),
        "profit_factor":   round(profit_factor, 4),
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
    }


def _build_yearly_returns(equity_curve: list[tuple[str, float]]) -> list[dict]:
    """Build per-year performance from equity curve."""
    if not equity_curve:
        return []

    df = pd.DataFrame(equity_curve, columns=["date", "equity"])
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year

    rows = []
    prev_eq = df.iloc[0]["equity"]
    prev_year = df.iloc[0]["year"]

    for year, grp in df.groupby("year"):
        start_eq = prev_eq
        end_eq   = grp.iloc[-1]["equity"]
        ret_pct  = (end_eq - start_eq) / start_eq * 100 if start_eq > 0 else 0.0
        rows.append({
            "year":       int(year),
            "return_pct": round(ret_pct, 2),
            "end_equity": round(end_eq, 2),
        })
        prev_eq = end_eq

    return rows


# ── Main backtester ───────────────────────────────────────────────────────────

class LongTermBacktester:
    """
    Simulate 10-15 years of daily-bar trading for up to 12 strategies.

    Entry: next day's open (after signal at close).
    Exit : stop / target vs day's H/L, else EOD close.
    """

    def __init__(
        self,
        strategies: list[str] | None = None,
        initial_capital: float = 25_000.0,
        max_risk_per_trade: float = 0.015,
        max_position_pct: float = 0.30,
        cache_dir: str = "./data_cache",
    ):
        self.strategies         = strategies or ALL_STRATEGIES
        self.initial_capital    = initial_capital
        self.max_risk_per_trade = max_risk_per_trade
        self.max_position_pct   = max_position_pct
        self.data_mgr           = HistoricalDataManager(cache_dir=cache_dir)

    # ── ATR-based stop/target helpers ────────────────────────────────────────

    @staticmethod
    def _stop_target(direction: str, entry: float, atr: float) -> tuple[float, float]:
        """
        1.5×ATR stop, 2.0×ATR target (R:R = 1.33:1, breakeven WR ~43%).

        Over MAX_HOLD_DAYS=5 days a 1.5 ATR adverse move is realistic
        (constitutes a trend reversal), and a 2.0 ATR favourable move is
        achievable on a solid trending day.  Tighter targets produce more
        winners vs the all-EOD-exit problem of wider multipliers.
        """
        if direction == "LONG":
            stop   = entry - 1.5 * atr
            target = entry + 2.0 * atr
        else:
            stop   = entry + 1.5 * atr
            target = entry - 2.0 * atr
        return stop, target

    # ── Run (swing-trade model: hold up to MAX_HOLD_DAYS, one trade at a time) ──

    MAX_HOLD_DAYS = 5  # max calendar trading days per trade

    def run(
        self,
        symbol: str = "SPY",
        start_date: str = "2010-01-01",
        end_date: str = "2024-12-31",
        use_cache: bool = True,
    ) -> LongTermResult:
        """
        Simulate swing trades on daily bars.

        Signal generated at close of day i  → enter at open of day i+1.
        Position held for up to MAX_HOLD_DAYS: stop/target checked each day,
        else exit at close on the last holding day.
        Only one trade open at a time (no overlapping positions).
        """
        df = self.data_mgr.fetch_daily_bars(symbol, start_date, end_date, use_cache=use_cache)
        if df.empty or len(df) < 30:
            raise ValueError(f"Insufficient data: {len(df)} bars")

        capital      = self.initial_capital
        all_trades:  list[DailyTrade] = []
        equity_curve: list[tuple[str, float]] = []

        rows = df.reset_index()  # date becomes a regular column

        # ── State machine: one trade at a time ───────────────────────────────
        in_trade      = False
        t_direction   = ""
        t_strategy    = ""
        t_entry       = 0.0
        t_stop        = 0.0
        t_target      = 0.0
        t_shares      = 0
        t_cap_before  = 0.0
        t_entry_date  = ""
        t_days_held   = 0
        # Credit-spread trade state
        t_is_credit_spread = False
        t_atr_at_entry     = 0.0
        t_credit_pct       = 0.0

        for i in range(1, len(rows)):
            today   = rows.iloc[i]
            prev    = rows.iloc[i - 1]
            date_str = str(today["date"])[:10]

            # ── Record daily equity ──────────────────────────────────────────
            equity_curve.append((date_str, round(capital, 2)))

            # ── If holding a position: check today's bar for exit ────────────
            if in_trade:
                t_days_held += 1

                # ── Credit-spread: hold 3 days then settle at expiry close ─────
                if t_is_credit_spread:
                    if t_days_held >= 3:
                        ep = today["close"]
                        pnl_per_contract, er = simulate_credit_spread(
                            t_direction,
                            t_entry,
                            ep,
                            t_atr_at_entry,
                            spread_width=5.0,
                            credit_pct=t_credit_pct,
                        )
                        # t_shares holds contract count; pnl is already in dollars
                        pnl     = pnl_per_contract * t_shares
                        pnl_pct = pnl / capital * 100 if capital else 0.0

                        capital_after = capital + pnl
                        if capital_after <= 0:
                            capital = 0.0
                            break
                        capital = capital_after

                        all_trades.append(DailyTrade(
                            date=date_str,
                            strategy=t_strategy,
                            direction=t_direction,
                            entry=round(t_entry, 4),
                            exit=round(ep, 4),
                            shares=t_shares,
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl_pct, 4),
                            exit_reason=er,
                            capital_before=round(t_cap_before, 2),
                            capital_after=round(capital, 2),
                        ))
                        in_trade = False
                    continue  # still holding days 1–2, or just closed on day 3

                # ── Standard swing-trade: stop/target vs H/L ─────────────────
                # Check stop/target against today's H/L
                ep, er = simulate_day_trade(
                    t_direction, t_entry,
                    today["high"], today["low"], today["close"],
                    t_stop, t_target,
                )

                # Force exit after MAX_HOLD_DAYS even if neither stop nor target
                if er not in ("stop", "target") and t_days_held >= self.MAX_HOLD_DAYS:
                    ep = today["close"]
                    er = "eod"

                if er in ("stop", "target", "eod") and (
                    er in ("stop", "target") or t_days_held >= self.MAX_HOLD_DAYS
                ):
                    # Close the trade
                    if t_direction == "LONG":
                        raw_pnl = (ep - t_entry) * t_shares
                    else:
                        raw_pnl = (t_entry - ep) * t_shares

                    commission = COMMISSION_PER_SHARE * t_shares
                    pnl     = raw_pnl - commission
                    pnl_pct = pnl / (t_entry * t_shares) * 100 if t_entry * t_shares else 0

                    capital_after = capital + pnl
                    if capital_after <= 0:
                        capital = 0.0
                        break
                    capital = capital_after

                    all_trades.append(DailyTrade(
                        date=date_str,
                        strategy=t_strategy,
                        direction=t_direction,
                        entry=round(t_entry, 4),
                        exit=round(ep, 4),
                        shares=t_shares,
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 4),
                        exit_reason=er,
                        capital_before=round(t_cap_before, 2),
                        capital_after=round(capital, 2),
                    ))
                    in_trade = False

                continue  # Either still holding or just closed — no new signal today

            # ── No open position: look for a signal on today's close ─────────
            # Need at least one more day (for entry open price)
            if i >= len(rows) - 1:
                continue

            direction      = None
            chosen_strategy = None
            for strat in self.strategies:
                fn = _SIGNAL_FUNCS.get(strat)
                if fn is None:
                    continue
                sig = fn(today, prev)
                if sig is not None:
                    direction = sig
                    chosen_strategy = strat
                    break

            if direction is None:
                continue

            # ── Regime filter: align trade direction with EMA200 trend ────────
            # In a bull trend (close > EMA200): skip SHORT signals (fading the trend)
            # In a bear trend (close < EMA200): skip LONG signals
            ema200 = today.get("ema200")
            if not pd.isna(ema200) and ema200 and ema200 > 0:
                if direction == "SHORT" and today["close"] > ema200:
                    continue  # Don't short in a bull trend
                if direction == "LONG" and today["close"] < ema200:
                    continue  # Don't go long in a bear trend

            # Entry at next day's open with slippage
            next_row   = rows.iloc[i + 1]
            entry_raw  = next_row["open"]
            if entry_raw <= 0:
                continue

            slip  = entry_raw * SLIPPAGE_BPS / 10_000
            entry = entry_raw + slip if direction == "LONG" else entry_raw - slip

            # ATR-based stop and target
            atr = today.get("atr14", entry * 0.01)
            if pd.isna(atr) or atr <= 0:
                atr = entry * 0.01
            stop, target = self._stop_target(direction, entry, atr)

            # ── Position sizing ───────────────────────────────────────────────
            is_credit = chosen_strategy in LT_ONLY_STRATEGIES
            if is_credit:
                # Size by max-loss per contract (not underlying shares)
                atr_pct_dec  = atr / entry                          # decimal, e.g. 0.008
                credit_pct   = max(0.06, min(0.12, atr_pct_dec * 8))
                max_loss_per = (5.0 - 5.0 * credit_pct) * 100      # dollars per contract
                risk_amt     = capital * self.max_risk_per_trade
                shares       = max(1, int(risk_amt / max_loss_per))  # contracts
                entry_credit_pct = credit_pct
            else:
                shares = _size_position(
                    capital, entry, stop,
                    self.max_risk_per_trade, self.max_position_pct,
                )
                entry_credit_pct = 0.0

            if shares <= 0:
                continue

            # Open the trade (first check happens on day i+1 in next iteration)
            in_trade           = True
            t_direction        = direction
            t_strategy         = chosen_strategy
            t_entry            = entry
            t_stop             = stop
            t_target           = target
            t_shares           = shares
            t_cap_before       = capital
            t_entry_date       = date_str
            t_days_held        = 0
            t_is_credit_spread = is_credit
            t_atr_at_entry     = atr
            t_credit_pct       = entry_credit_pct

        # Final equity point
        last_date = str(rows["date"].iloc[-1])[:10]
        if not equity_curve or equity_curve[-1][0] != last_date:
            equity_curve.append((last_date, round(capital, 2)))

        # Years tested
        start_dt = pd.to_datetime(start_date)
        end_dt   = pd.to_datetime(end_date)
        years_tested = (end_dt - start_dt).days / 365.25

        # Compute metrics
        metrics = _compute_extended_metrics(all_trades, equity_curve, self.initial_capital, years_tested)
        yearly  = _build_yearly_returns(equity_curve)

        # Add trade count per year to yearly
        trade_df = pd.DataFrame([{"date": t.date, "pnl": t.pnl} for t in all_trades])
        if not trade_df.empty:
            trade_df["year"] = pd.to_datetime(trade_df["date"]).dt.year
            trade_counts = trade_df.groupby("year").size().to_dict()
            for yr in yearly:
                yr["trades"] = trade_counts.get(yr["year"], 0)
        else:
            for yr in yearly:
                yr["trades"] = 0

        return LongTermResult(
            cagr_pct=metrics["cagr_pct"],
            sharpe_ratio=metrics["sharpe_ratio"],
            sortino_ratio=metrics["sortino_ratio"],
            calmar_ratio=metrics["calmar_ratio"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
            total_return_pct=metrics["total_return_pct"],
            win_rate=metrics["win_rate"],
            total_trades=metrics["total_trades"],
            profit_factor=metrics["profit_factor"],
            avg_win=metrics["avg_win"],
            avg_loss=metrics["avg_loss"],
            final_capital=round(capital, 2),
            years_tested=round(years_tested, 2),
            equity_curve=[{"date": d, "equity": e} for d, e in equity_curve],
            yearly_returns=yearly,
            trades=[vars(t) for t in all_trades[-500:]],  # last 500 for response size
        )
