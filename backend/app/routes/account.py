"""Account routes: info, P&L, risk metrics."""

from __future__ import annotations
from fastapi import APIRouter
from app.services.trading_engine import trading_engine
from app.services.schwab_client import schwab_client
from app.schemas import AccountInfo, RiskMetrics

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/info", response_model=AccountInfo)
async def get_account_info():
    pe = trading_engine.paper_engine
    trades = pe.closed_trades
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total = len(trades)
    win_rate = wins / total if total > 0 else 0.0

    return AccountInfo(
        equity=round(pe.capital, 2),
        cash=round(pe.capital, 2),
        buying_power=round(pe.capital, 2),
        peak_equity=round(pe.peak_capital, 2),
        drawdown_pct=round(pe.drawdown_pct * 100, 2),
        daily_pnl=round(pe.daily_pnl, 2),
        total_pnl=round(pe.capital - pe.initial_capital, 2),
        win_rate=round(win_rate, 4),
        total_trades=total,
    )


@router.get("/risk", response_model=RiskMetrics)
async def get_risk_metrics():
    pe = trading_engine.paper_engine
    rm = trading_engine.risk_manager
    metrics = rm.get_metrics(
        pe.capital, pe.peak_capital, pe.daily_pnl, pe.trades_today
    )
    return RiskMetrics(**metrics)


@router.get("/performance")
async def get_daily_performance():
    """Return daily P&L breakdown."""
    from collections import defaultdict
    from datetime import datetime

    daily = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
    for t in trading_engine.paper_engine.closed_trades:
        date = datetime.fromisoformat(t["exit_time"]).strftime("%Y-%m-%d")
        daily[date]["pnl"] += t["pnl"]
        daily[date]["trades"] += 1
        if t["pnl"] > 0:
            daily[date]["wins"] += 1
        else:
            daily[date]["losses"] += 1

    return {
        "daily": [
            {"date": d, **vals} for d, vals in sorted(daily.items())
        ]
    }
