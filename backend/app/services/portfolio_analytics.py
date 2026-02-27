"""Portfolio-level analytics: CVaR, Omega ratio, Ulcer index, Monte Carlo stress tests.

Provides institutional-grade risk metrics computed purely from closed-trade history
and current open-position Greeks — no external dependencies beyond NumPy.

Key metrics:
  cvar_95       Conditional Value at Risk at 95% confidence (expected tail loss).
  omega_ratio   Omega(0): gains above threshold / losses below threshold.
  ulcer_index   Root-mean-square of percentage drawdowns from running peak.
  monte_carlo   Simulated N-day P&L distribution (bootstrap resampling).
  greeks        Aggregate delta, gamma, theta, vega across all open legs.
  delta_notional Portfolio delta in dollar terms (delta × contracts × multiplier × SPY price).
"""

from __future__ import annotations
import math
import random
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── Metric computation ─────────────────────────────────────────────────────────

def compute_cvar(returns: list[float], confidence: float = 0.95) -> float:
    """Conditional Value at Risk (Expected Shortfall) at the given confidence level.

    Returns the average loss in the worst (1-confidence) fraction of outcomes.
    Convention: positive number = expected loss (e.g. 450 means $450 average tail loss).
    Returns 0.0 when insufficient data (<5 observations).
    """
    if len(returns) < 5:
        return 0.0
    arr = np.array(returns, dtype=float)
    cutoff = np.percentile(arr, (1 - confidence) * 100)
    tail = arr[arr <= cutoff]
    if len(tail) == 0:
        return 0.0
    return float(-tail.mean())  # positive number representing expected tail loss


def compute_omega_ratio(returns: list[float], threshold: float = 0.0) -> float:
    """Omega ratio: probability-weighted gains above threshold / losses below threshold.

    Values > 1 indicate more probability mass above the threshold than below.
    Returns 1.0 when insufficient data.
    """
    if len(returns) < 5:
        return 1.0
    arr = np.array(returns, dtype=float)
    gains = arr[arr > threshold] - threshold
    losses = threshold - arr[arr <= threshold]
    gain_sum = float(gains.sum()) if len(gains) > 0 else 0.0
    loss_sum = float(losses.sum()) if len(losses) > 0 else 1e-9
    return round(gain_sum / loss_sum, 4)


def compute_ulcer_index(equity_curve: list[float]) -> float:
    """Ulcer Index: root-mean-square of percentage drawdowns from running peak.

    Lower is better (0 = no drawdowns).  Sensitive to depth AND duration.
    """
    if len(equity_curve) < 2:
        return 0.0
    arr = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(arr)
    pct_dd = np.where(peak > 0, (arr - peak) / peak * 100, 0.0)
    return float(np.sqrt(np.mean(pct_dd ** 2)))


def compute_sortino(returns: list[float], risk_free: float = 0.0) -> float:
    """Sortino ratio: mean excess return / downside deviation."""
    if len(returns) < 5:
        return 0.0
    arr = np.array(returns, dtype=float)
    excess = arr - risk_free
    downside = arr[arr < risk_free]
    if len(downside) == 0:
        return float("inf")
    downside_std = float(np.std(downside))
    if downside_std == 0:
        return 0.0
    return round(float(np.mean(excess)) / downside_std, 4)


def run_monte_carlo(
    returns: list[float],
    initial_capital: float,
    n_simulations: int = 2000,
    n_days: int = 21,   # ~1 trading month
) -> dict:
    """Bootstrap Monte Carlo simulation of N-day forward P&L.

    Resamples from the empirical return distribution (with replacement).
    Returns the simulated equity percentile bands.

    Returns:
        {
          "n_simulations": int,
          "n_days": int,
          "p5":  float,  # 5th percentile ending equity
          "p25": float,
          "p50": float,  # median
          "p75": float,
          "p95": float,  # 95th percentile
          "prob_loss": float,  # probability of losing money
          "prob_dd_5pct": float,  # probability of >5% drawdown during simulation
        }
    """
    if len(returns) < 10:
        return {
            "n_simulations": 0, "n_days": n_days,
            "p5": initial_capital, "p25": initial_capital,
            "p50": initial_capital, "p75": initial_capital, "p95": initial_capital,
            "prob_loss": 0.0, "prob_dd_5pct": 0.0,
        }

    arr = np.array(returns, dtype=float)
    rng = np.random.default_rng(seed=42)

    ending_equities: list[float] = []
    max_drawdowns: list[float] = []

    for _ in range(n_simulations):
        # Sample n_days returns with replacement
        sampled = rng.choice(arr, size=n_days, replace=True)
        # Simulate equity curve (each return is a P&L dollar amount)
        equity = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for r in sampled:
            equity += r
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        ending_equities.append(equity)
        max_drawdowns.append(max_dd)

    eq_arr = np.array(ending_equities)
    dd_arr = np.array(max_drawdowns)

    return {
        "n_simulations": n_simulations,
        "n_days": n_days,
        "p5":  round(float(np.percentile(eq_arr, 5)), 2),
        "p25": round(float(np.percentile(eq_arr, 25)), 2),
        "p50": round(float(np.percentile(eq_arr, 50)), 2),
        "p75": round(float(np.percentile(eq_arr, 75)), 2),
        "p95": round(float(np.percentile(eq_arr, 95)), 2),
        "prob_loss": round(float((eq_arr < initial_capital).mean()), 4),
        "prob_dd_5pct": round(float((dd_arr > 0.05).mean()), 4),
    }


def compute_greeks_exposure(position) -> dict:
    """Aggregate portfolio Greeks from an open PaperOptionPosition.

    Returns per-share Greeks summed across all legs, scaled by contracts × 100.
    """
    if position is None:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "net_delta_notional": 0.0}

    total_delta = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    total_vega = 0.0

    for leg in position.order.legs:
        sign = -1.0 if "SELL" in leg.action.value else 1.0
        total_delta += sign * leg.delta * leg.quantity
        total_gamma += sign * leg.gamma * leg.quantity
        total_theta += sign * leg.theta * leg.quantity
        total_vega  += sign * leg.vega  * leg.quantity

    underlying = position.entry_underlying
    net_delta_notional = total_delta * underlying * 100  # dollar delta exposure

    return {
        "delta": round(total_delta, 4),
        "gamma": round(total_gamma, 6),
        "theta": round(total_theta, 4),
        "vega":  round(total_vega,  4),
        "net_delta_notional": round(net_delta_notional, 2),
    }


def compute_rolling_performance(
    closed_trades: list[dict],
    lookback_days: int = 90,
) -> dict[str, dict]:
    """Compute per-strategy rolling performance over the last `lookback_days` trading days.

    Returns a mapping of strategy_name -> {win_rate, profit_factor, total_pnl, trades}.
    Strategies with < 5 trades in the window are flagged as `insufficient_data`.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    strategy_trades: dict[str, list[float]] = {}
    for trade in closed_trades:
        try:
            exit_time_str = trade.get("exit_time", "")
            if not exit_time_str:
                continue
            exit_dt = datetime.fromisoformat(exit_time_str)
            # Make timezone-aware if needed
            if exit_dt.tzinfo is None:
                from zoneinfo import ZoneInfo
                exit_dt = exit_dt.replace(tzinfo=ZoneInfo("America/New_York"))
            if exit_dt < cutoff:
                continue
            strat = trade.get("strategy", "unknown")
            pnl = trade.get("pnl", 0.0)
            strategy_trades.setdefault(strat, []).append(float(pnl))
        except Exception:
            continue

    result = {}
    for strat, pnls in strategy_trades.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = abs(float(np.mean(losses))) if losses else 0.0
        pf = (sum(wins) / abs(sum(losses))) if losses else (sum(wins) if wins else 0.0)
        result[strat] = {
            "trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
            "profit_factor": round(pf, 4),
            "total_pnl": round(sum(pnls), 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "cvar_95": round(compute_cvar(pnls), 2),
            "omega": round(compute_omega_ratio(pnls), 4),
            "insufficient_data": len(pnls) < 5,
            "retire_recommended": (
                len(pnls) >= 10 and (
                    (len(wins) / len(pnls)) < 0.50      # WR < 50%
                    or sum(pnls) < 0                     # negative total P&L
                    or pf < 1.0                          # losing profit factor
                )
            ),
        }

    return result


# ── Main portfolio snapshot ────────────────────────────────────────────────────

def get_portfolio_snapshot(
    paper_engine,
    initial_capital: float,
    current_spy_price: float,
) -> dict:
    """Build a complete portfolio analytics snapshot for the API response.

    Accepts the PaperOptionsEngine instance and returns all metrics.
    """
    closed_trades = paper_engine.closed_trades
    returns = [t.get("pnl", 0.0) for t in closed_trades if t.get("pnl") is not None]

    # Build equity curve from cumulative P&L
    equity_curve = []
    running_equity = initial_capital
    for t in closed_trades:
        pnl = t.get("pnl", 0.0) or 0.0
        running_equity += float(pnl)
        equity_curve.append(running_equity)

    # Current open position equity
    current_equity = paper_engine.total_equity(current_spy_price)

    greeks = compute_greeks_exposure(paper_engine.position)
    mc = run_monte_carlo(returns, current_equity)
    rolling = compute_rolling_performance(closed_trades, lookback_days=90)

    # Strategy retirement recommendations
    retire_list = [s for s, m in rolling.items() if m.get("retire_recommended")]

    return {
        "equity": round(current_equity, 2),
        "initial_capital": round(initial_capital, 2),
        "total_pnl": round(current_equity - initial_capital, 2),
        "total_trades": len(returns),
        "cvar_95": round(compute_cvar(returns), 2),
        "omega_ratio": round(compute_omega_ratio(returns), 4),
        "ulcer_index": round(compute_ulcer_index(equity_curve if equity_curve else [initial_capital]), 4),
        "sortino_ratio": round(compute_sortino(returns), 4),
        "greeks": greeks,
        "delta_adjusted_exposure_pct": round(
            abs(greeks["net_delta_notional"]) / current_equity * 100, 2
        ) if current_equity > 0 else 0.0,
        "monte_carlo": mc,
        "rolling_90d": rolling,
        "retire_recommendations": retire_list,
    }
