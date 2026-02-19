"""Account routes: info, P&L, risk metrics."""

from __future__ import annotations
from fastapi import APIRouter
from app.services.trading_engine import trading_engine
from app.schemas import AccountInfo, RiskMetrics

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/info", response_model=AccountInfo)
async def get_account_info():
    pe = trading_engine.paper_engine

    # Use in-memory trades for the current session
    trades = pe.closed_trades

    # If in-memory is empty, also query DB for historical data
    if not trades:
        from app.database import async_session
        from app.models import Trade as TradeModel
        from sqlalchemy import select, func
        async with async_session() as db:
            count_stmt = select(func.count()).select_from(TradeModel).where(TradeModel.status == "CLOSED")
            total_result = await db.execute(count_stmt)
            total = total_result.scalar() or 0

            win_stmt = select(func.count()).select_from(TradeModel).where(
                TradeModel.status == "CLOSED", TradeModel.pnl > 0
            )
            win_result = await db.execute(win_stmt)
            wins = win_result.scalar() or 0

            pnl_stmt = select(func.sum(TradeModel.pnl)).where(TradeModel.status == "CLOSED")
            pnl_result = await db.execute(pnl_stmt)
            total_pnl_db = pnl_result.scalar() or 0.0

            # Daily P&L from DB
            from datetime import datetime
            from zoneinfo import ZoneInfo
            ET = ZoneInfo("America/New_York")
            today = datetime.now(ET).date()
            today_start = datetime.combine(today, datetime.min.time())
            daily_stmt = select(func.sum(TradeModel.pnl)).where(
                TradeModel.status == "CLOSED",
                TradeModel.exit_time >= today_start,
            )
            daily_result = await db.execute(daily_stmt)
            daily_pnl_db = daily_result.scalar() or 0.0

            # Trades today from DB
            today_count_stmt = select(func.count()).select_from(TradeModel).where(
                TradeModel.status == "CLOSED",
                TradeModel.exit_time >= today_start,
            )
            today_count_result = await db.execute(today_count_stmt)
            trades_today_db = today_count_result.scalar() or 0

            win_rate = wins / total if total > 0 else 0.0

            return AccountInfo(
                equity=round(pe.capital, 2),
                cash=round(pe.capital, 2),
                buying_power=round(pe.capital, 2),
                peak_equity=round(pe.peak_capital, 2),
                drawdown_pct=round(pe.drawdown_pct * 100, 2),
                daily_pnl=round(daily_pnl_db + pe.daily_pnl, 2),
                total_pnl=round(total_pnl_db + (pe.capital - pe.initial_capital), 2),
                win_rate=round(win_rate, 4),
                total_trades=total,
            )

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

    trades_today = pe.trades_today
    daily_pnl = pe.daily_pnl

    # If in-memory is empty, check DB for today's data
    if trades_today == 0 or daily_pnl == 0.0:
        from app.database import async_session
        from app.models import Trade as TradeModel
        from sqlalchemy import select, func
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        today = datetime.now(ET).date()
        today_start = datetime.combine(today, datetime.min.time())
        async with async_session() as db:
            if trades_today == 0:
                stmt = select(func.count()).select_from(TradeModel).where(
                    TradeModel.status == "CLOSED",
                    TradeModel.exit_time >= today_start,
                )
                result = await db.execute(stmt)
                trades_today = result.scalar() or 0

            if daily_pnl == 0.0:
                pnl_stmt = select(func.sum(TradeModel.pnl)).where(
                    TradeModel.status == "CLOSED",
                    TradeModel.exit_time >= today_start,
                )
                pnl_result = await db.execute(pnl_stmt)
                daily_pnl = pnl_result.scalar() or 0.0

    metrics = rm.get_metrics(
        pe.capital, pe.peak_capital, daily_pnl, trades_today
    )
    return RiskMetrics(**metrics)


@router.get("/performance")
async def get_daily_performance():
    """Return daily P&L breakdown."""
    from collections import defaultdict
    from datetime import datetime

    daily = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})

    # In-memory trades
    for t in trading_engine.paper_engine.closed_trades:
        date = datetime.fromisoformat(t["exit_time"]).strftime("%Y-%m-%d")
        daily[date]["pnl"] += t["pnl"]
        daily[date]["trades"] += 1
        if t["pnl"] > 0:
            daily[date]["wins"] += 1
        else:
            daily[date]["losses"] += 1

    # Also read from DB if in-memory is empty
    if not daily:
        from app.database import async_session
        from app.models import Trade as TradeModel
        from sqlalchemy import select
        async with async_session() as db:
            stmt = select(TradeModel).where(TradeModel.status == "CLOSED").order_by(TradeModel.exit_time)
            result = await db.execute(stmt)
            for t in result.scalars().all():
                if t.exit_time:
                    date = t.exit_time.strftime("%Y-%m-%d")
                    daily[date]["pnl"] += t.pnl or 0.0
                    daily[date]["trades"] += 1
                    if (t.pnl or 0) > 0:
                        daily[date]["wins"] += 1
                    else:
                        daily[date]["losses"] += 1

    return {
        "daily": [
            {"date": d, **vals} for d, vals in sorted(daily.items())
        ]
    }


@router.get("/analytics/mae-mfe")
async def get_mae_mfe_analytics():
    """MAE/MFE analytics grouped by strategy."""
    from app.database import async_session
    from app.models import Trade as TradeModel
    from sqlalchemy import select, func

    async with async_session() as db:
        stmt = (
            select(
                TradeModel.strategy,
                func.count(TradeModel.id).label("trade_count"),
                func.avg(TradeModel.mae).label("avg_mae"),
                func.avg(TradeModel.mfe).label("avg_mfe"),
                func.avg(TradeModel.mae_pct).label("avg_mae_pct"),
                func.avg(TradeModel.mfe_pct).label("avg_mfe_pct"),
                func.avg(TradeModel.bars_held).label("avg_bars_held"),
                func.avg(TradeModel.confidence).label("avg_confidence"),
            )
            .where(TradeModel.status == "CLOSED")
            .where(TradeModel.mae.isnot(None))
            .group_by(TradeModel.strategy)
        )
        result = await db.execute(stmt)
        rows = result.all()

    analytics = []
    for row in rows:
        analytics.append({
            "strategy": row.strategy,
            "trade_count": row.trade_count,
            "avg_mae": round(row.avg_mae, 2) if row.avg_mae else 0,
            "avg_mfe": round(row.avg_mfe, 2) if row.avg_mfe else 0,
            "avg_mae_pct": round(row.avg_mae_pct, 3) if row.avg_mae_pct else 0,
            "avg_mfe_pct": round(row.avg_mfe_pct, 3) if row.avg_mfe_pct else 0,
            "avg_bars_held": round(row.avg_bars_held, 1) if row.avg_bars_held else 0,
            "avg_confidence": round(row.avg_confidence, 3) if row.avg_confidence else 0,
        })

    return {"analytics": analytics}
