"""Trading routes: start/stop bot, get trades, set mode."""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.services.trading_engine import trading_engine
from app.schemas import BotStatus, TradingModeUpdate

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status", response_model=BotStatus)
async def get_status():
    status = trading_engine.get_status()
    return BotStatus(**status)


@router.post("/start")
async def start_bot():
    if trading_engine.running:
        raise HTTPException(400, "Bot is already running")
    await trading_engine.start()
    return {"status": "started", "mode": trading_engine.mode}


@router.post("/stop")
async def stop_bot():
    if not trading_engine.running:
        raise HTTPException(400, "Bot is not running")
    await trading_engine.stop()
    return {"status": "stopped"}


@router.post("/mode")
async def set_mode(update: TradingModeUpdate):
    if update.mode not in ("paper", "live"):
        raise HTTPException(400, "Mode must be 'paper' or 'live'")
    if update.mode == "live":
        if update.confirmation != "I understand the risks of live trading":
            raise HTTPException(
                400,
                "To enable live trading, set confirmation to: "
                "'I understand the risks of live trading'"
            )
    trading_engine.set_mode(update.mode)
    return {"mode": trading_engine.mode}


@router.get("/trades")
async def get_trades(limit: int = 50):
    from app.database import async_session
    from app.models import Trade as TradeModel
    from app.schemas import TradeOut
    from sqlalchemy import select, func

    async with async_session() as db:
        # Count total
        count_stmt = select(func.count()).select_from(TradeModel).where(TradeModel.status == "CLOSED")
        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        # Fetch recent trades
        stmt = (
            select(TradeModel)
            .where(TradeModel.status == "CLOSED")
            .order_by(TradeModel.exit_time.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        trades = result.scalars().all()

    # Fall back to in-memory if DB is empty (backward compat during migration)
    if total == 0 and trading_engine.paper_engine.closed_trades:
        mem_trades = trading_engine.paper_engine.closed_trades[-limit:]
        mem_trades_copy = list(reversed(mem_trades))
        return {"trades": mem_trades_copy, "total": len(trading_engine.paper_engine.closed_trades)}

    return {
        "trades": [TradeOut.model_validate(t) for t in trades],
        "total": total,
    }


@router.get("/position")
async def get_position():
    pos = trading_engine.paper_engine.position
    if pos is None:
        return {"position": None}

    # Use last known market price for unrealized P&L
    current_price = pos.entry_price
    df = trading_engine._df_1min
    if df is not None and not df.empty:
        current_price = float(df.iloc[-1]["close"])

    return {
        "position": {
            "symbol": pos.symbol,
            "direction": pos.direction,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "entry_time": pos.entry_time.isoformat(),
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "strategy": pos.strategy,
            "unrealized_pnl": round(pos.unrealized_pnl(current_price), 2),
            "original_quantity": pos.original_quantity,
            "scales_completed": list(pos.scales_completed),
            "effective_stop": pos.effective_stop,
        }
    }
