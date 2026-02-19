"""Leaderboard routes: strategy rankings, comparison, auto-backtest progress."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import StrategyRanking, BacktestRun
from app.schemas import StrategyRankingOut, LeaderboardResponse, StrategyComparisonOut
from app.services.auto_backtester import auto_backtester

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


@router.get("/rankings", response_model=LeaderboardResponse)
async def get_rankings(db: AsyncSession = Depends(get_db)):
    stmt = select(StrategyRanking).order_by(StrategyRanking.composite_score.desc())
    result = await db.execute(stmt)
    rankings = [StrategyRankingOut.model_validate(r) for r in result.scalars().all()]
    return LeaderboardResponse(rankings=rankings, progress=auto_backtester.progress)


@router.get("/comparison", response_model=list[StrategyComparisonOut])
async def get_comparison(db: AsyncSession = Depends(get_db)):
    """Per-strategy, per-date-range breakdown from recent backtest runs."""
    stmt = (
        select(BacktestRun)
        .order_by(BacktestRun.created_at.desc())
        .limit(200)
    )
    result = await db.execute(stmt)
    runs = result.scalars().all()

    comparisons = []
    for run in runs:
        strats = run.strategies or ""
        # Only include individual strategy runs
        if "," in strats:
            continue

        # Determine date range label
        label = "custom"
        if run.interval == "5m":
            label = "30d"
        elif run.start_date and run.end_date:
            try:
                from datetime import datetime as dt
                start = dt.strptime(run.start_date, "%Y-%m-%d")
                end = dt.strptime(run.end_date, "%Y-%m-%d")
                days = (end - start).days
                if days <= 2:
                    label = "1d"
                elif days <= 8:
                    label = "5d"
                else:
                    label = "30d"
            except (ValueError, TypeError):
                pass

        comparisons.append(StrategyComparisonOut(
            strategy=strats,
            date_range=label,
            start_date=run.start_date or "",
            end_date=run.end_date or "",
            total_trades=run.total_trades or 0,
            win_rate=run.win_rate or 0.0,
            total_return_pct=run.total_return_pct or 0.0,
            sharpe_ratio=run.sharpe_ratio or 0.0,
            max_drawdown_pct=run.max_drawdown_pct or 0.0,
            profit_factor=run.profit_factor or 0.0,
        ))

    return comparisons


@router.get("/progress")
async def get_progress():
    return auto_backtester.progress


@router.post("/trigger")
async def trigger_backtest():
    await auto_backtester.trigger()
    return {"status": "triggered", "progress": auto_backtester.progress}
