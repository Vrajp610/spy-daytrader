"""Trade memory — cosine-similarity search over historical trade contexts.

How it works
------------
Every closed trade is encoded into a 9-dimensional numerical feature vector.
Before each new entry, we query the K most similar past trades and compute their
win rate and average P&L.  If similar setups have a poor historical record, the
trading engine reduces the entry confidence or skips the trade entirely.

Feature vector (all dimensions normalized to ≈ [0, 1]):
  [0] confidence        — signal confidence at entry
  [1] |entry_delta|     — absolute delta (0 → 0.50+ mapped to 0 → 1)
  [2] entry_iv          — implied volatility (0 → 1.5 mapped to 0 → 1)
  [3] |entry_theta|     — daily theta decay (0 → 0.05 mapped to 0 → 1)
  [4] hour_norm         — hour of day: 9:30=0.0, 16:00=1.0
  [5] day_norm          — day of week: Monday=0.0, Friday=1.0
  [6] is_credit         — 1.0 if credit spread, 0.0 if debit
  [7] regime_enc        — TRENDING_UP=0, TRENDING_DOWN=0.33, RANGE_BOUND=0.67, VOLATILE=1.0
  [8] max_loss_norm     — max_loss / $1000 (capped at 1.0)

Usage in trading_engine
------------------------
  from app.services.trade_memory import query_similar_trades

  result = query_similar_trades(
      closed_trades=self.paper_engine.closed_trades,
      context=current_trade_context,
  )
  if result["win_rate"] is not None and result["win_rate"] < 0.30:
      # Similar past setups lost 70%+ of the time — skip or reduce size
      ...
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Minimum number of similar trades required before we trust the verdict
MIN_SIMILAR_TRADES = 5

# How many neighbors to pull (more = smoother estimate, but may include less-similar)
DEFAULT_K = 20

# Regime → float encoding
_REGIME_ENC: dict[str, float] = {
    "TRENDING_UP":   0.00,
    "TRENDING_DOWN": 0.33,
    "RANGE_BOUND":   0.67,
    "VOLATILE":      1.00,
}


# ── Encoding ──────────────────────────────────────────────────────────────────

def _encode(trade: dict) -> Optional[np.ndarray]:
    """
    Convert a closed-trade dict (or a current-context dict) to a feature vector.
    Returns None if required fields are missing or invalid.
    """
    try:
        # Hour of day from entry_time
        entry_time_str = trade.get("entry_time", "")
        if entry_time_str:
            entry_dt = datetime.fromisoformat(entry_time_str)
            hour_frac = entry_dt.hour + entry_dt.minute / 60.0
            hour_norm = max(0.0, min(1.0, (hour_frac - 9.5) / 6.5))  # 9:30→0, 16:00→1
            day_norm = entry_dt.weekday() / 4.0  # Mon=0, Fri=1
        else:
            hour_norm = 0.5
            day_norm = 0.5

        # Credit vs debit
        opt_type = (trade.get("option_strategy_type") or "").upper()
        is_credit = 1.0 if "CREDIT" in opt_type else 0.0

        # Regime
        regime_str = (trade.get("regime") or "RANGE_BOUND").upper()
        regime_enc = _REGIME_ENC.get(regime_str, 0.67)

        # Numerical fields with safe defaults
        confidence  = float(trade.get("confidence") or 0.70)
        entry_delta = abs(float(trade.get("entry_delta") or 0.20))
        entry_iv    = float(trade.get("entry_iv") or 0.20)
        entry_theta = abs(float(trade.get("entry_theta") or 0.01))
        max_loss    = float(trade.get("max_loss") or 200.0)

        return np.array([
            min(max(confidence, 0.0), 1.0),              # [0] confidence
            min(entry_delta, 0.50) / 0.50,               # [1] |delta| → 0-1
            min(entry_iv, 1.50) / 1.50,                  # [2] IV → 0-1
            min(entry_theta, 0.05) / 0.05,               # [3] |theta| → 0-1
            hour_norm,                                    # [4] hour
            day_norm,                                     # [5] day
            is_credit,                                    # [6] credit flag
            regime_enc,                                   # [7] regime
            min(max_loss / 1000.0, 1.0),                 # [8] max_loss
        ], dtype=np.float32)

    except Exception as exc:
        logger.debug(f"trade_memory._encode failed: {exc}")
        return None


# ── Query ─────────────────────────────────────────────────────────────────────

def query_similar_trades(
    closed_trades: list[dict],
    context: dict,
    k: int = DEFAULT_K,
    min_sample: int = MIN_SIMILAR_TRADES,
) -> dict:
    """
    Find the K most similar past trades to `context` and return their outcome stats.

    Parameters
    ----------
    closed_trades : list of trade dicts from paper_engine.closed_trades
    context       : dict with the same fields as a trade dict (entry_time,
                    confidence, entry_delta, entry_iv, entry_theta, max_loss,
                    option_strategy_type, regime)
    k             : number of neighbours to consider
    min_sample    : minimum neighbours needed before returning a verdict

    Returns
    -------
    {
      "similar_count": int,
      "win_rate": float | None,    # None when insufficient data
      "avg_pnl": float | None,
      "verdict": "PENALISE" | "BLOCK" | "OK" | "INSUFFICIENT_DATA",
      "confidence_multiplier": float,   # 1.0 = no change, <1.0 = penalise
    }
    """
    query_vec = _encode(context)
    if query_vec is None or len(closed_trades) < min_sample:
        return _insufficient()

    # Encode all past trades
    vecs: list[np.ndarray] = []
    pnls: list[float] = []
    for t in closed_trades:
        v = _encode(t)
        if v is not None:
            vecs.append(v)
            pnls.append(float(t.get("pnl") or 0.0))

    if len(vecs) < min_sample:
        return _insufficient()

    # Cosine similarity
    X = np.stack(vecs, axis=0)                                # (N, 9)
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    X_norm = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)
    sims = X_norm @ q_norm                                    # (N,)

    actual_k = min(k, len(sims))
    top_idx = np.argsort(sims)[-actual_k:]
    similar_pnls = [pnls[i] for i in top_idx]
    avg_sim = float(sims[top_idx].mean())

    wins = sum(1 for p in similar_pnls if p > 0)
    win_rate = wins / len(similar_pnls)
    avg_pnl = sum(similar_pnls) / len(similar_pnls)

    # Verdict logic
    if len(similar_pnls) < min_sample or avg_sim < 0.80:
        # Low similarity — not enough context to penalise
        verdict = "OK"
        multiplier = 1.0
    elif win_rate < 0.25:
        # Similar setups lost >75% of the time — block
        verdict = "BLOCK"
        multiplier = 0.0
        logger.info(
            f"TradeMemory: BLOCK — similar setups WR={win_rate:.0%} "
            f"avg_pnl=${avg_pnl:.0f} (n={len(similar_pnls)}, sim={avg_sim:.2f})"
        )
    elif win_rate < 0.40:
        # Similar setups struggling — reduce confidence by 25%
        verdict = "PENALISE"
        multiplier = 0.75
        logger.info(
            f"TradeMemory: PENALISE — similar setups WR={win_rate:.0%} "
            f"avg_pnl=${avg_pnl:.0f} (n={len(similar_pnls)}, sim={avg_sim:.2f})"
        )
    else:
        verdict = "OK"
        multiplier = 1.0
        if win_rate >= 0.60:
            logger.debug(
                f"TradeMemory: OK — similar setups WR={win_rate:.0%} "
                f"avg_pnl=${avg_pnl:.0f} (n={len(similar_pnls)})"
            )

    return {
        "similar_count": len(similar_pnls),
        "win_rate": round(win_rate, 3),
        "avg_pnl": round(avg_pnl, 2),
        "avg_similarity": round(avg_sim, 3),
        "verdict": verdict,
        "confidence_multiplier": multiplier,
    }


def _insufficient() -> dict:
    return {
        "similar_count": 0,
        "win_rate": None,
        "avg_pnl": None,
        "avg_similarity": None,
        "verdict": "INSUFFICIENT_DATA",
        "confidence_multiplier": 1.0,
    }
