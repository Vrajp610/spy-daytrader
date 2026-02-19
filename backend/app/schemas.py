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
    confidence: Optional[float] = None
    slippage: Optional[float] = None
    commission: Optional[float] = None
    mae: Optional[float] = None
    mfe: Optional[float] = None
    mae_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    bars_held: Optional[int] = None

    model_config = {"from_attributes": True}


class BotStatus(BaseModel):
    running: bool
    mode: str  # paper / live
    current_regime: Optional[str] = None
    open_position: Optional[dict] = None
    daily_pnl: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None
    equity: Optional[float] = None
    peak_equity: Optional[float] = None
    drawdown_pct: Optional[float] = None


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
    strategies: list[str] = [
        "vwap_reversion", "orb", "ema_crossover", "volume_flow", "mtf_momentum",
        "rsi_divergence", "bb_squeeze", "macd_reversal", "momentum_scalper",
        "gap_fill", "micro_pullback", "double_bottom_top",
    ]
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


# ── Leaderboard ─────────────────────────────────────────────────────────

class StrategyRankingOut(BaseModel):
    strategy_name: str
    avg_sharpe_ratio: float
    avg_profit_factor: float
    avg_win_rate: float
    avg_return_pct: float
    avg_max_drawdown_pct: float
    composite_score: float
    total_backtest_trades: int
    backtest_count: int
    computed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class LeaderboardResponse(BaseModel):
    rankings: list[StrategyRankingOut]
    progress: dict


class StrategyComparisonOut(BaseModel):
    strategy: str
    date_range: str
    start_date: str
    end_date: str
    total_trades: int
    win_rate: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    profit_factor: float


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


# ── Trading Settings ─────────────────────────────────────────────────────────

class TradingSettingsOut(BaseModel):
    initial_capital: float
    max_risk_per_trade: float
    daily_loss_limit: float
    max_drawdown: float
    max_position_pct: float
    max_trades_per_day: int
    cooldown_after_consecutive_losses: int
    cooldown_minutes: int
    min_signal_confidence: float


class TradingSettingsUpdate(BaseModel):
    initial_capital: Optional[float] = None
    max_risk_per_trade: Optional[float] = None
    daily_loss_limit: Optional[float] = None
    max_drawdown: Optional[float] = None
    max_position_pct: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    cooldown_after_consecutive_losses: Optional[int] = None
    cooldown_minutes: Optional[int] = None
    min_signal_confidence: Optional[float] = None


# ── WebSocket ────────────────────────────────────────────────────────────────

class WSMessage(BaseModel):
    type: str  # price_update, trade_update, status_update, error
    data: dict
