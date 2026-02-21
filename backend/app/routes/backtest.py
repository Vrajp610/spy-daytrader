"""Backtest routes: run backtests, get results."""

from __future__ import annotations
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import BacktestRun
from app.schemas import BacktestRequest, BacktestResult, LongTermBacktestRequest, LongTermBacktestResult
from app.services.backtester import Backtester
from app.services.long_term_backtester import LongTermBacktester

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestResult)
async def run_backtest(req: BacktestRequest, db: AsyncSession = Depends(get_db)):
    # Validate dates
    if req.start_date >= req.end_date:
        raise HTTPException(400, "start_date must be before end_date")

    try:
        bt = Backtester(
            strategies=req.strategies,
            initial_capital=req.initial_capital,
            use_regime_filter=req.use_regime_filter,
        )
        # Run CPU-bound backtest in thread pool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: bt.run(
                symbol="SPY",
                start_date=req.start_date,
                end_date=req.end_date,
                interval=req.interval,
            ),
        )

        # Save to DB
        run = BacktestRun(
            symbol="SPY",
            start_date=req.start_date,
            end_date=req.end_date,
            interval=req.interval,
            initial_capital=req.initial_capital,
            strategies=",".join(req.strategies),
            total_return_pct=result.total_return_pct,
            win_rate=result.win_rate,
            total_trades=result.total_trades,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown_pct=result.max_drawdown_pct,
            profit_factor=result.profit_factor,
            avg_win=result.avg_win,
            avg_loss=result.avg_loss,
            equity_curve=result.equity_curve,
            trades_json=result.trades,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        return BacktestResult.model_validate(run)

    except Exception as e:
        raise HTTPException(500, f"Backtest failed: {str(e)}")


@router.get("/results", response_model=list[BacktestResult])
async def list_results(limit: int = 20, db: AsyncSession = Depends(get_db)):
    stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    runs = result.scalars().all()
    return [BacktestResult.model_validate(r) for r in runs]


@router.get("/results/{run_id}", response_model=BacktestResult)
async def get_result(run_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(BacktestRun).where(BacktestRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Backtest run not found")
    return BacktestResult.model_validate(run)


@router.post("/long-term", response_model=LongTermBacktestResult)
async def run_long_term_backtest(req: LongTermBacktestRequest):
    """Run a long-term (daily bars) backtest. Results are NOT persisted to DB."""
    if req.start_date >= req.end_date:
        raise HTTPException(400, "start_date must be before end_date")

    try:
        bt = LongTermBacktester(
            strategies=req.strategies,
            initial_capital=req.initial_capital,
            max_risk_per_trade=req.max_risk_per_trade,
            cache_dir=settings.data_cache_dir,
        )
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: bt.run(
                symbol="SPY",
                start_date=req.start_date,
                end_date=req.end_date,
                use_cache=True,
            ),
        )
        return LongTermBacktestResult(**vars(result))
    except Exception as e:
        raise HTTPException(500, f"Long-term backtest failed: {str(e)}")


@router.get("/data-cache-status")
async def data_cache_status():
    """List CSV files in the local data cache directory."""
    try:
        from app.services.historical_data import HistoricalDataManager
        mgr = HistoricalDataManager(cache_dir=settings.data_cache_dir)
        return {"files": mgr.list_cache_files(), "cache_dir": settings.data_cache_dir}
    except Exception as e:
        raise HTTPException(500, f"Cache status failed: {str(e)}")
