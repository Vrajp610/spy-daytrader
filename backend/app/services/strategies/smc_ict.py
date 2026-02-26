"""ICT Smart Money Concepts strategy — full 5-confluence A+/A/B rating system.

Based on the Inner Circle Trader (ICT) methodology:

Confluence 1 — HTF PD Array Rejection (OB / FVG / Liquidity Sweep) on 15M+
  • Order Block  : Last opposing candle body before a 3-bar impulse.
  • Fair Value Gap: Three-candle gap where price is expected to retrace.
  • Liquidity Sweep: Price raids a prior swing H/L, wicks through, reverses.
  → Price must be AT / rejecting FROM the PD array zone.

Confluence 2 — Internal SMT Divergence (1–5M TF)
  • SPY makes a lower low but QQQ does NOT (bullish SMT trap).
  • SPY makes a higher high but QQQ does NOT (bearish SMT trap).
  • Fallback when QQQ unavailable: compare 1M price swing vs 5M swing structure.

Confluence 3 — External SMT Divergence (15M TF and above)
  • Same logic as C2 but on 15M / 1H timeframe.
  • Fallback: price/RSI hidden divergence on the higher-timeframe lookback.

Confluence 4 — IFVG (Imbalance Fair Value Gap) on 1–3M
  • A small FVG in the most recent 10 bars that price is at or inside.
  • Confirms institutional order flow before continuation.

Confluence 5 — External Liquidity Target (EQH / EQL / Session H-L)
  • Equal Highs (EQH): Two+ swing highs within 0.15% of each other → buy-side liq above.
  • Equal Lows (EQL): Two+ swing lows within 0.15% of each other → sell-side liq below.
  • Session levels: prev-day H/L, overnight H/L, NY open level.
  • Trade must be TARGETING these levels (not just near them).

Rating → Confidence mapping
  A+ (5/5 aligned) : 0.92
  A  (4/5 aligned) : 0.86
  B  (3/5 aligned) : 0.79
  < 3              : no trade
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.strategies.base import (
    BaseStrategy, TradeSignal, ExitSignal, Direction,
)
from app.services.strategies.regime_detector import MarketRegime

ET = ZoneInfo("America/New_York")


# ── Tunable constants ──────────────────────────────────────────────────────────

_OB_IMPULSE_BARS   = 3        # min bars in the impulse that defines an OB
_OB_LOOKBACK       = 40       # bars to search for OBs
_FVG_LOOKBACK      = 50       # bars to search for FVGs
_FVG_NEAR_PCT      = 0.0030   # price must be within 0.30% of FVG edge
_LS_LOOKBACK       = 30       # bars for liquidity-sweep swing reference
_SMT_LOOKBACK      = 25       # bars for SMT swing comparison
_EQH_TOL_PCT       = 0.0015   # 0.15% tolerance for equal-highs / equal-lows
_EQL_TOL_PCT       = 0.0015
_HTF_BARS_15M      = 15       # 1-min bars per 15-minute pseudo-TF bar
_HTF_BARS_1H       = 60       # 1-min bars per 1-hour pseudo-TF bar
_SESSION_NY_OPEN   = time(9, 30)
_SESSION_NY_CLOSE  = time(16, 0)
_SWING_PIVOT_N     = 3        # swing H/L pivot look-left / look-right


# ── Helper dataclasses (plain dicts for speed) ─────────────────────────────────

def _fvg_result(active: bool = False, direction: str = "LONG",
                top: float = 0.0, bottom: float = 0.0) -> dict:
    return {"active": active, "direction": direction, "top": top, "bottom": bottom}


def _ob_result(active: bool = False, direction: str = "LONG",
               high: float = 0.0, low: float = 0.0) -> dict:
    return {"active": active, "direction": direction, "high": high, "low": low}


def _bool_result(active: bool = False, direction: str = "LONG") -> dict:
    return {"active": active, "direction": direction}


# ── Pure detection functions ───────────────────────────────────────────────────

def _swing_highs(df: pd.DataFrame, n: int = _SWING_PIVOT_N) -> list[float]:
    """Return swing-high values using a simple pivot: higher than N bars each side."""
    highs = df["high"].values
    pivots = []
    for i in range(n, len(highs) - n):
        if all(highs[i] >= highs[i - j] for j in range(1, n + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, n + 1)):
            pivots.append(float(highs[i]))
    return pivots


def _swing_lows(df: pd.DataFrame, n: int = _SWING_PIVOT_N) -> list[float]:
    lows = df["low"].values
    pivots = []
    for i in range(n, len(lows) - n):
        if all(lows[i] <= lows[i - j] for j in range(1, n + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, n + 1)):
            pivots.append(float(lows[i]))
    return pivots


def detect_fvg(df: pd.DataFrame, close: float) -> dict:
    """
    Find the most recent unfilled FVG in `df` that price is at or inside.

    Bullish FVG: gap between bar[i-1].high and bar[i+1].low  (price dropped back into it)
    Bearish FVG: gap between bar[i-1].low and bar[i+1].high  (price rallied back into it)
    """
    n = len(df)
    if n < 3:
        return _fvg_result()

    limit = min(n - 1, _FVG_LOOKBACK)
    for i in range(n - 2, n - limit, -1):
        if i < 1:
            break
        hi_prev  = float(df.iloc[i - 1]["high"])
        lo_prev  = float(df.iloc[i - 1]["low"])
        hi_next  = float(df.iloc[i + 1]["high"]) if i + 1 < n else 0.0
        lo_next  = float(df.iloc[i + 1]["low"])  if i + 1 < n else 0.0

        # Bullish FVG: candle[i-1] high < candle[i+1] low
        if hi_prev < lo_next:
            gap_top    = lo_next
            gap_bottom = hi_prev
            # Price should be at / below the gap top (about to be filled from below)
            if gap_bottom <= close <= gap_top * (1 + _FVG_NEAR_PCT):
                return _fvg_result(True, "LONG", gap_top, gap_bottom)

        # Bearish FVG: candle[i-1] low > candle[i+1] high
        if lo_prev > hi_next:
            gap_top    = lo_prev
            gap_bottom = hi_next
            # Price should be at / above the gap bottom
            if gap_bottom * (1 - _FVG_NEAR_PCT) <= close <= gap_top:
                return _fvg_result(True, "SHORT", gap_top, gap_bottom)

    return _fvg_result()


def detect_order_block(df: pd.DataFrame, close: float) -> dict:
    """
    Find the most recent Order Block that price is currently testing.

    Bullish OB: last bearish candle before a consecutive 3-bar upward impulse,
                price now back inside the OB body.
    Bearish OB: last bullish candle before a consecutive 3-bar downward impulse.
    """
    n = len(df)
    if n < _OB_LOOKBACK:
        return _ob_result()

    for i in range(n - _OB_IMPULSE_BARS - 1, n - _OB_LOOKBACK, -1):
        if i < 0:
            break
        # Bullish OB: bearish candle at i, followed by ≥3 bullish candles
        if df.iloc[i]["close"] < df.iloc[i]["open"]:
            impulse_up = all(
                df.iloc[i + k]["close"] > df.iloc[i + k]["open"]
                for k in range(1, _OB_IMPULSE_BARS + 1)
                if i + k < n
            )
            if impulse_up:
                ob_high = float(df.iloc[i]["open"])    # body top of bearish OB
                ob_low  = float(df.iloc[i]["close"])   # body bottom
                if ob_low * (1 - 0.002) <= close <= ob_high * (1 + 0.002):
                    return _ob_result(True, "LONG", ob_high, ob_low)

        # Bearish OB: bullish candle at i, followed by ≥3 bearish candles
        if df.iloc[i]["close"] > df.iloc[i]["open"]:
            impulse_dn = all(
                df.iloc[i + k]["close"] < df.iloc[i + k]["open"]
                for k in range(1, _OB_IMPULSE_BARS + 1)
                if i + k < n
            )
            if impulse_dn:
                ob_high = float(df.iloc[i]["close"])   # body top of bullish OB
                ob_low  = float(df.iloc[i]["open"])    # body bottom
                if ob_low * (1 - 0.002) <= close <= ob_high * (1 + 0.002):
                    return _ob_result(True, "SHORT", ob_high, ob_low)

    return _ob_result()


def detect_liquidity_sweep(df: pd.DataFrame) -> dict:
    """
    Detect a recent liquidity sweep (stop-hunt + reversal).

    Bullish sweep: bar wicks BELOW the N-bar low and CLOSES back above it
                   → sell-side liquidity grabbed, expect reversal up.
    Bearish sweep: bar wicks ABOVE the N-bar high and CLOSES back below it
                   → buy-side liquidity grabbed, expect reversal down.

    Checks the most recent 5 bars for a sweep signal.
    """
    n = len(df)
    if n < _LS_LOOKBACK + 5:
        return _bool_result()

    for i in range(n - 1, n - 6, -1):
        window = df.iloc[max(0, i - _LS_LOOKBACK): i]
        if window.empty:
            continue
        swing_low  = float(window["low"].min())
        swing_high = float(window["high"].max())
        bar_low    = float(df.iloc[i]["low"])
        bar_high   = float(df.iloc[i]["high"])
        bar_close  = float(df.iloc[i]["close"])

        # Bullish sweep: wick below prior low, close back above
        if bar_low < swing_low and bar_close > swing_low:
            return _bool_result(True, "LONG")
        # Bearish sweep: wick above prior high, close back below
        if bar_high > swing_high and bar_close < swing_high:
            return _bool_result(True, "SHORT")

    return _bool_result()


def detect_smt_divergence(df_a: pd.DataFrame, df_b: Optional[pd.DataFrame]) -> dict:
    """
    Smart Money Trap (SMT) divergence between two correlated instruments.

    Bullish SMT: A makes a lower swing-low while B does NOT → bullish trap in A.
    Bearish SMT: A makes a higher swing-high while B does NOT → bearish trap in A.

    When df_b is None, uses price-vs-RSI hidden divergence as a proxy:
      • Hidden bullish: price lower low, RSI higher low.
      • Hidden bearish: price higher high, RSI lower high.
    """
    n = len(df_a)
    if n < _SMT_LOOKBACK:
        return _bool_result()

    window_a = df_a.iloc[max(0, n - _SMT_LOOKBACK):]
    close_a = window_a["close"].values

    if df_b is not None and len(df_b) >= _SMT_LOOKBACK:
        window_b = df_b.iloc[max(0, len(df_b) - _SMT_LOOKBACK):]
        close_b  = window_b["close"].values

        # Align lengths
        min_len = min(len(close_a), len(close_b))
        close_a = close_a[-min_len:]
        close_b = close_b[-min_len:]

        # Split into first half (reference) and second half (current)
        mid = min_len // 2
        prev_low_a  = float(close_a[:mid].min())
        cur_low_a   = float(close_a[mid:].min())
        prev_low_b  = float(close_b[:mid].min())
        cur_low_b   = float(close_b[mid:].min())
        prev_high_a = float(close_a[:mid].max())
        cur_high_a  = float(close_a[mid:].max())
        prev_high_b = float(close_b[:mid].max())
        cur_high_b  = float(close_b[mid:].max())

        # Bullish SMT: A lower low, B higher low (B does not confirm)
        if cur_low_a < prev_low_a * 0.998 and cur_low_b > prev_low_b * 0.998:
            return _bool_result(True, "LONG")
        # Bearish SMT: A higher high, B lower high (B does not confirm)
        if cur_high_a > prev_high_a * 1.002 and cur_high_b < prev_high_b * 1.002:
            return _bool_result(True, "SHORT")
    else:
        # Proxy: price vs RSI divergence
        if "rsi" not in window_a.columns:
            return _bool_result()
        rsi_a    = window_a["rsi"].values
        mid      = len(close_a) // 2
        if mid < 3:
            return _bool_result()

        prev_low  = float(close_a[:mid].min())
        cur_low   = float(close_a[mid:].min())
        prev_low_rsi  = float(rsi_a[:mid].min())
        cur_low_rsi   = float(rsi_a[mid:].min())
        prev_high = float(close_a[:mid].max())
        cur_high  = float(close_a[mid:].max())
        prev_high_rsi = float(rsi_a[:mid].max())
        cur_high_rsi  = float(rsi_a[mid:].max())

        # Hidden bullish: price lower low, RSI higher low
        if cur_low < prev_low * 0.999 and cur_low_rsi > prev_low_rsi * 1.02:
            return _bool_result(True, "LONG")
        # Hidden bearish: price higher high, RSI lower high
        if cur_high > prev_high * 1.001 and cur_high_rsi < prev_high_rsi * 0.98:
            return _bool_result(True, "SHORT")

    return _bool_result()


def detect_ifvg(df: pd.DataFrame, close: float, lookback: int = 10) -> dict:
    """
    Imbalance FVG (IFVG): a small FVG in the last `lookback` bars.
    Price must currently be inside or at the edge of the gap.
    """
    n = len(df)
    if n < 3:
        return _fvg_result()

    start = max(1, n - lookback)
    for i in range(n - 2, start, -1):
        hi_prev = float(df.iloc[i - 1]["high"])
        lo_prev = float(df.iloc[i - 1]["low"])
        hi_next = float(df.iloc[i + 1]["high"]) if i + 1 < n else 0.0
        lo_next = float(df.iloc[i + 1]["low"])  if i + 1 < n else 0.0

        # Bullish IFVG
        if hi_prev < lo_next:
            if hi_prev <= close <= lo_next * (1 + _FVG_NEAR_PCT):
                return _fvg_result(True, "LONG", lo_next, hi_prev)
        # Bearish IFVG
        if lo_prev > hi_next:
            if hi_next * (1 - _FVG_NEAR_PCT) <= close <= lo_prev:
                return _fvg_result(True, "SHORT", lo_prev, hi_next)

    return _fvg_result()


def detect_external_liquidity(
    df: pd.DataFrame,
    close: float,
    current_time: Optional[datetime] = None,
) -> dict:
    """
    Detect external liquidity pools and determine if price is TARGETING them.

    Pools:
    • Equal Highs (EQH): 2+ swing highs within 0.15% → buy-side liquidity above.
    • Equal Lows  (EQL): 2+ swing lows  within 0.15% → sell-side liquidity below.
    • Previous-day high / low.
    • Session open level (NY 9:30 AM).

    If price is BELOW EQH / prev-day-high and trending toward it → LONG target.
    If price is ABOVE EQL / prev-day-low and trending toward it → SHORT target.
    """
    n = len(df)
    if n < 30:
        return _bool_result()

    lookback_df = df.iloc[max(0, n - 120):]

    # ── Swing highs/lows ──────────────────────────────────────────────────────
    sh_list = _swing_highs(lookback_df)
    sl_list = _swing_lows(lookback_df)

    # ── Equal Highs ───────────────────────────────────────────────────────────
    eqh_levels: list[float] = []
    for i, h1 in enumerate(sh_list):
        for h2 in sh_list[i + 1:]:
            if abs(h1 - h2) / h1 <= _EQH_TOL_PCT:
                eqh_levels.append((h1 + h2) / 2)
                break

    # ── Equal Lows ────────────────────────────────────────────────────────────
    eql_levels: list[float] = []
    for i, l1 in enumerate(sl_list):
        for l2 in sl_list[i + 1:]:
            if abs(l1 - l2) / l1 <= _EQL_TOL_PCT:
                eql_levels.append((l1 + l2) / 2)
                break

    # ── Session / prev-day levels ─────────────────────────────────────────────
    prev_day_levels: list[tuple[str, float]] = []
    if current_time is not None:
        today_date = current_time.date()
        prev_mask = [
            ts.date() < today_date
            for ts in pd.to_datetime(df.index)
        ] if hasattr(df.index[0], "date") else []
        if any(prev_mask):
            prev_df = df[[m for m in prev_mask]]   # type: ignore
            try:
                prev_df = df[prev_mask]
                if not prev_df.empty:
                    prev_day_levels.append(("PDH", float(prev_df["high"].max())))
                    prev_day_levels.append(("PDL", float(prev_df["low"].min())))
            except Exception:
                pass

    # ── Momentum proxy: last 10 bars trending up/down ─────────────────────────
    recent = df.iloc[max(0, n - 10):]
    if len(recent) >= 2:
        trend_up = float(recent.iloc[-1]["close"]) > float(recent.iloc[0]["close"])
    else:
        trend_up = True

    # ── Determine direction from targeting ────────────────────────────────────
    # LONG if price is below an EQH or PDH (targeting that level)
    for level in eqh_levels:
        if close < level * (1 - 0.001) and trend_up:
            return _bool_result(True, "LONG")
    for name, level in prev_day_levels:
        if name == "PDH" and close < level * (1 - 0.001) and trend_up:
            return _bool_result(True, "LONG")

    # SHORT if price is above an EQL or PDL (targeting that level)
    for level in eql_levels:
        if close > level * (1 + 0.001) and not trend_up:
            return _bool_result(True, "SHORT")
    for name, level in prev_day_levels:
        if name == "PDL" and close > level * (1 + 0.001) and not trend_up:
            return _bool_result(True, "SHORT")

    return _bool_result()


# ── HTF helper: resample 1-min df to pseudo-HTF bars ─────────────────────────

def _resample_htf(df_1m: pd.DataFrame, bars_per_htf: int) -> pd.DataFrame:
    """
    Aggregate consecutive groups of `bars_per_htf` 1-min bars into OHLCV rows.
    Used when real 15M/1H data is unavailable (backtester mode).
    """
    n = len(df_1m)
    if n < bars_per_htf * 4:
        return pd.DataFrame()
    rows = []
    for start in range(0, n - bars_per_htf, bars_per_htf):
        chunk = df_1m.iloc[start: start + bars_per_htf]
        rows.append({
            "open":   float(chunk.iloc[0]["open"]),
            "high":   float(chunk["high"].max()),
            "low":    float(chunk["low"].min()),
            "close":  float(chunk.iloc[-1]["close"]),
            "volume": float(chunk["volume"].sum()),
            "rsi":    float(chunk["rsi"].iloc[-1]) if "rsi" in chunk.columns else 50.0,
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ── Main strategy ─────────────────────────────────────────────────────────────

class SMCICTStrategy(BaseStrategy):
    """
    Full ICT Smart Money Concepts strategy.

    Fires on 3–5 aligned confluences with a clear direction.
    Returns None unless at least 3 confluences agree.
    """

    name = "smc_ict"

    # Which confluences count; must have at least MIN_CONFLUENCES
    MIN_CONFLUENCES = 3

    def default_params(self) -> dict:
        return {}

    def generate_signal(
        self,
        df: pd.DataFrame,
        idx: int,
        current_time: datetime,
        **kwargs,
    ) -> Optional[TradeSignal]:

        if df is None or len(df) < 60:
            return None

        ctx           = kwargs.get("market_context")
        df_qqq_5m     = kwargs.get("df_qqq_5min")
        df_qqq_15m    = kwargs.get("df_qqq_15min")

        # Skip VOLATILE — liquidity grabs happen but spreads blow out
        regime = ctx.regime if ctx is not None else MarketRegime.RANGE_BOUND
        if regime == MarketRegime.VOLATILE:
            return None

        # Working slice up to current bar
        df_work   = df.iloc[: idx + 1]
        close     = float(df.iloc[idx]["close"])

        # ── Resolve HTF data ───────────────────────────────────────────────────
        if ctx is not None and not ctx.df_15min.empty:
            df_15m = ctx.df_15min
        else:
            df_15m = _resample_htf(df_work, _HTF_BARS_15M)

        if ctx is not None and not ctx.df_1hr.empty:
            df_1h = ctx.df_1hr
        else:
            df_1h = _resample_htf(df_work, _HTF_BARS_1H)

        htf_df = df_15m if not (
            isinstance(df_15m, pd.DataFrame) and df_15m.empty
        ) else df_1h

        # ── 1. HTF PD Array Rejection ──────────────────────────────────────────
        htf_fvg = detect_fvg(htf_df, close)         if isinstance(htf_df, pd.DataFrame) and not htf_df.empty else _fvg_result()
        htf_ob  = detect_order_block(htf_df, close) if isinstance(htf_df, pd.DataFrame) and not htf_df.empty else _ob_result()
        htf_ls  = detect_liquidity_sweep(htf_df)    if isinstance(htf_df, pd.DataFrame) and not htf_df.empty else _bool_result()

        htf_active    = htf_fvg["active"] or htf_ob["active"] or htf_ls["active"]
        htf_direction = (
            htf_fvg["direction"] if htf_fvg["active"] else
            htf_ob["direction"]  if htf_ob["active"]  else
            htf_ls["direction"]  if htf_ls["active"]  else None
        )

        # ── 2. Internal SMT (1–5M) ─────────────────────────────────────────────
        if ctx is not None and not ctx.df_5min.empty and df_qqq_5m is not None:
            smt_int = detect_smt_divergence(ctx.df_5min, df_qqq_5m)
        elif ctx is not None and not ctx.df_5min.empty:
            smt_int = detect_smt_divergence(ctx.df_5min, None)
        else:
            smt_int = detect_smt_divergence(df_work.iloc[-_SMT_LOOKBACK:], None)

        # ── 3. External SMT (15M+) ─────────────────────────────────────────────
        if not (isinstance(htf_df, pd.DataFrame) and htf_df.empty) and df_qqq_15m is not None:
            smt_ext = detect_smt_divergence(htf_df, df_qqq_15m)
        elif not (isinstance(htf_df, pd.DataFrame) and htf_df.empty):
            smt_ext = detect_smt_divergence(htf_df, None)
        else:
            smt_ext = _bool_result()

        # ── 4. IFVG (1–3M) ────────────────────────────────────────────────────
        ifvg = detect_ifvg(df_work, close, lookback=10)

        # ── 5. External Liquidity ─────────────────────────────────────────────
        ext_liq = detect_external_liquidity(df_work, close, current_time)

        # ── Score confluences (direction must agree) ───────────────────────────
        confluences = [
            ("htf_pd_array",      htf_active,       htf_direction),
            ("internal_smt",      smt_int["active"], smt_int["direction"]),
            ("external_smt",      smt_ext["active"], smt_ext["direction"]),
            ("ifvg_1_3m",         ifvg["active"],    ifvg["direction"]),
            ("external_liquidity",ext_liq["active"], ext_liq["direction"]),
        ]

        active = [(name, d) for name, a, d in confluences if a and d is not None]
        if not active:
            return None

        long_count  = sum(1 for _, d in active if d == "LONG")
        short_count = sum(1 for _, d in active if d == "SHORT")

        if long_count >= short_count and long_count >= self.MIN_CONFLUENCES:
            direction   = Direction.LONG
            score_count = long_count
        elif short_count > long_count and short_count >= self.MIN_CONFLUENCES:
            direction   = Direction.SHORT
            score_count = short_count
        else:
            return None

        # ── Rating and confidence ──────────────────────────────────────────────
        if score_count >= 5:
            rating, confidence = "A+", 0.92
        elif score_count == 4:
            rating, confidence = "A",  0.86
        else:
            rating, confidence = "B",  0.79

        active_names = [n for n, d in active if d == direction.value]

        # ── ATR-based stop / target ────────────────────────────────────────────
        atr = float(df.iloc[idx].get("atr", close * 0.005))
        if direction == Direction.LONG:
            stop_loss   = close - 1.5 * atr
            take_profit = close + 3.0 * atr   # 2:1 R:R minimum
        else:
            stop_loss   = close + 1.5 * atr
            take_profit = close - 3.0 * atr

        return TradeSignal(
            strategy=self.name,
            direction=direction,
            entry_price=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            timestamp=current_time,
            metadata={
                "rating":              rating,
                "confluences":         score_count,
                "active_confluences":  active_names,
                "htf_fvg_active":      htf_fvg["active"],
                "htf_ob_active":       htf_ob["active"],
                "htf_ls_active":       htf_ls["active"],
                "internal_smt":        smt_int["active"],
                "external_smt":        smt_ext["active"],
                "ifvg_active":         ifvg["active"],
                "ext_liquidity":       ext_liq["active"],
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
        """
        Exit rules:
        1. Opposing liquidity sweep invalidates the trade.
        2. Options engine handles premium-based exits.
        """
        if df is None or len(df) < 10:
            return None

        close = float(df.iloc[idx]["close"])
        sweep = detect_liquidity_sweep(df.iloc[max(0, idx - 10): idx + 1])

        if sweep["active"]:
            opposing = (
                sweep["direction"] == "SHORT" and trade.direction == Direction.LONG or
                sweep["direction"] == "LONG"  and trade.direction == Direction.SHORT
            )
            if opposing:
                return ExitSignal(
                    reason="opposing_liquidity_sweep",
                    exit_price=close,
                    timestamp=current_time,
                )
        return None
