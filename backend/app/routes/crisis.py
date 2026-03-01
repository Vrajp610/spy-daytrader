"""Crisis-period backtesting API routes."""

from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.crisis_backtester import crisis_backtester, CRISIS_WINDOWS
from app.services.long_term_backtester import ALL_STRATEGIES

router = APIRouter(prefix="/crisis", tags=["crisis"])


class CrisisBacktestRequest(BaseModel):
    strategies: List[str] = Field(default_factory=lambda: list(ALL_STRATEGIES))
    initial_capital: float = 50_000.0
    windows: Optional[List[str]] = None  # None = all 6 windows


@router.post("")
async def start_crisis_backtest(req: CrisisBacktestRequest):
    """Start a crisis-period backtest in the background."""
    # Validate window IDs if provided
    if req.windows:
        invalid = [w for w in req.windows if w not in CRISIS_WINDOWS]
        if invalid:
            raise HTTPException(status_code=422, detail=f"Unknown window IDs: {invalid}")

    await crisis_backtester.run(
        strategies=req.strategies,
        initial_capital=req.initial_capital,
        windows=req.windows,
    )
    return {"status": "started"}


@router.get("/progress")
async def get_crisis_progress():
    """Poll crisis backtest progress."""
    return crisis_backtester.progress


@router.get("/result")
async def get_crisis_result():
    """Return the completed crisis backtest result or 404 if not ready."""
    result = crisis_backtester.result
    if result is None:
        raise HTTPException(status_code=404, detail="No crisis backtest result available yet")
    return result
