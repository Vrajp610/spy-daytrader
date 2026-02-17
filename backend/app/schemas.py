"""Pydantic schemas for API requests/responses."""

from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel


# ── Trading ──────────────────────────────────────────────────────────────────

class TradeOut(BaseModel):
    id: int
    symbol: str
    direction: str
    strategy: str
    regime: Optional[str] = None
    quantity: int
    entry_price: float
    entry_time: datetime
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    is_paper: bool
    status: str

    model_config = {"from_attributes": True}


class BotStatus(BaseModel):
    running: bool
    mode: str  # paper / live
    current_regime: Optional[str] = None
    open_position: Optional[TradeOut] = None
    daily_pnl: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None


class TradingModeUpdate(BaseModel):
    mode: str  # paper / live
    confirmation: Optional[str] = None  # required when switching to live


# ── Account ──────────────────────────────────────────────────────────────────

class AccountInfo(BaseModel):
    equity: float
    cash: float
    buying_power: float
    peak_equity: float
    drawdown_pct: float
    daily_pnl: float
    total_pnl: float
    win_rate: float
    total_trades: int


class DailyPerformanceOut(BaseModel):
    date: date
    starting_capital: float
    ending_capital: float
    realized_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    regime: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Backtest ─────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str
    interval: str = "1m"
    initial_capital: float = 25000.0
    strategies: list[str] = ["vwap_reversion", "orb", "ema_crossover"]
    use_regime_filter: bool = True


class BacktestResult(BaseModel):
    id: int
    created_at: datetime
    start_date: str
    end_date: str
    strategies: str
    initial_capital: float
    total_return_pct: Optional[float] = None
    win_rate: Optional[float] = None
    total_trades: Optional[int] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    equity_curve: Optional[list] = None
    trades_json: Optional[list] = None

    model_config = {"from_attributes": True}


# ── Strategy Config ──────────────────────────────────────────────────────────

class StrategyConfigOut(BaseModel):
    id: int
    name: str
    enabled: bool
    params: dict

    model_config = {"from_attributes": True}


class StrategyConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    params: Optional[dict] = None


# ── Risk ─────────────────────────────────────────────────────────────────────

class RiskMetrics(BaseModel):
    current_drawdown_pct: float
    max_drawdown_limit: float
    daily_loss: float
    daily_loss_limit: float
    trades_today: int
    max_trades_per_day: int
    consecutive_losses: int
    cooldown_active: bool
    circuit_breaker_active: bool


# ── WebSocket ────────────────────────────────────────────────────────────────

class WSMessage(BaseModel):
    type: str  # price_update, trade_update, status_update, error
    data: dict
