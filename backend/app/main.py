"""FastAPI application entry point."""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.websocket import ws_manager
from app.routes import trading, backtest, account, settings as settings_routes
from app.routes import leaderboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SPY DayTrader backend...")
    await init_db()
    logger.info("Database initialized")

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
