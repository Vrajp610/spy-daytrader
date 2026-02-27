"""Portfolio analytics API routes.

GET /api/analytics/portfolio
    Full portfolio snapshot: CVaR, Omega ratio, Ulcer index, Monte Carlo,
    portfolio Greeks, rolling 90-day per-strategy performance, retirement recommendations.

GET /api/analytics/monte-carlo
    Lightweight endpoint that re-runs the Monte Carlo simulation on demand
    with optional `n_simulations` and `n_days` query parameters.

GET /api/analytics/greeks
    Current open-position Greeks only (fast, called on every dashboard refresh).
"""

from __future__ import annotations
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _get_engine():
    """Lazy import to avoid circular dependencies at module load time."""
    from app.services.trading_engine import trading_engine
    return trading_engine


@router.get("/portfolio")
async def get_portfolio_analytics():
    """Full portfolio analytics snapshot."""
    engine = _get_engine()
    from app.services.portfolio_analytics import get_portfolio_snapshot
    from app.config import settings

    current_price = engine._get_last_price() or 500.0
    snapshot = get_portfolio_snapshot(
        paper_engine=engine.paper_engine,
        initial_capital=settings.initial_capital,
        current_spy_price=current_price,
    )
    return snapshot


@router.get("/monte-carlo")
async def run_monte_carlo_endpoint(
    n_simulations: int = Query(default=2000, ge=100, le=10000),
    n_days: int = Query(default=21, ge=1, le=252),
):
    """Re-run Monte Carlo with custom parameters."""
    engine = _get_engine()
    from app.services.portfolio_analytics import run_monte_carlo
    from app.config import settings

    returns = [
        t.get("pnl", 0.0)
        for t in engine.paper_engine.closed_trades
        if t.get("pnl") is not None
    ]
    current_price = engine._get_last_price() or 500.0
    current_equity = engine.paper_engine.total_equity(current_price)

    return run_monte_carlo(
        returns=returns,
        initial_capital=current_equity,
        n_simulations=n_simulations,
        n_days=n_days,
    )


@router.get("/greeks")
async def get_greeks():
    """Current open-position Greeks (fast endpoint for dashboard polling)."""
    engine = _get_engine()
    from app.services.portfolio_analytics import compute_greeks_exposure

    greeks = compute_greeks_exposure(engine.paper_engine.position)
    greeks["portfolio_net_delta"] = engine.paper_engine.portfolio_net_delta
    greeks["open_position"] = engine.paper_engine.position is not None
    return greeks


@router.get("/rolling-performance")
async def get_rolling_performance(
    lookback_days: int = Query(default=90, ge=7, le=365),
):
    """Per-strategy rolling performance over the last N trading days."""
    engine = _get_engine()
    from app.services.portfolio_analytics import compute_rolling_performance

    return compute_rolling_performance(
        engine.paper_engine.closed_trades,
        lookback_days=lookback_days,
    )
