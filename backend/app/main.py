"""FastAPI application entry point."""

from __future__ import annotations
import logging
import json as _json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.websocket import ws_manager
from app.routes import trading, backtest, account, settings as settings_routes
from app.routes import leaderboard
from app.routes import analytics
from app.routes import webhook


class StructuredFormatter(logging.Formatter):
    """JSON-structured log output for production monitoring."""
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include extra fields if present
        for field in ("trade_id", "strategy", "correlation_id"):
            val = getattr(record, field, None)
            if val is not None:
                log_entry[field] = val
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return _json.dumps(log_entry)


handler = logging.StreamHandler()
handler.setFormatter(StructuredFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SPY DayTrader backend...")
    await init_db()
    logger.info("Database initialized")

    # Load persisted trading settings from DB
    from app.routes.settings import load_trading_config_from_db
    await load_trading_config_from_db()

    # Load per-strategy live performance history from DB
    from app.services.strategy_monitor import strategy_monitor
    from app.database import async_session
    async with async_session() as db:
        await strategy_monitor.load_from_db(db)
    logger.info("Strategy monitor loaded from DB")

    from app.services.auto_backtester import auto_backtester
    from app.services.trading_engine import trading_engine

    await auto_backtester.start()
    logger.info("Auto-backtester started")

    await trading_engine.start()
    logger.info("Trading engine started")

    yield

    logger.info("Shutting down...")
    await auto_backtester.stop()
    if trading_engine.running:
        await trading_engine.stop()


app = FastAPI(
    title="SPY DayTrader",
    description="Automated SPY daytrading platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(trading.router)
app.include_router(backtest.router)
app.include_router(account.router)
app.include_router(settings_routes.router)
app.include_router(leaderboard.router)
app.include_router(analytics.router)
app.include_router(webhook.router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Client can send ping/pong or commands
            if data == "ping":
                await websocket.send_text('{"type":"pong","data":{}}')
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": settings.trading_mode}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
