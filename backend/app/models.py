"""SQLAlchemy ORM models."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Boolean, Text, Date, JSON,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, default="SPY", nullable=False)
    direction = Column(String, nullable=False)  # LONG / SHORT
    strategy = Column(String, nullable=False)
    regime = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    exit_reason = Column(String, nullable=True)
    is_paper = Column(Boolean, default=True, nullable=False)
    status = Column(String, default="OPEN", nullable=False)  # OPEN / CLOSED
    confidence = Column(Float, nullable=True)
    slippage = Column(Float, nullable=True)
    commission = Column(Float, default=0.0)
    vix_at_entry = Column(Float, nullable=True)
    bars_held = Column(Integer, nullable=True)
    mae = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    mae_pct = Column(Float, nullable=True)
    mfe_pct = Column(Float, nullable=True)
    # Options fields
    option_strategy_type = Column(String, nullable=True)  # LONG_CALL, IRON_CONDOR, etc.
    contract_symbol = Column(String, nullable=True)       # OCC symbol
    legs_json = Column(Text, nullable=True)               # JSON array of leg details
    strike = Column(Float, nullable=True)                 # primary strike
    expiration_date = Column(String, nullable=True)       # YYYY-MM-DD
    option_type = Column(String, nullable=True)           # CALL/PUT
    net_premium = Column(Float, nullable=True)            # premium per spread
    max_loss = Column(Float, nullable=True)               # defined risk
    max_profit = Column(Float, nullable=True)             # defined reward
    entry_delta = Column(Float, nullable=True)
    entry_theta = Column(Float, nullable=True)
    entry_iv = Column(Float, nullable=True)
    underlying_entry = Column(Float, nullable=True)       # SPY price at entry
    underlying_exit = Column(Float, nullable=True)        # SPY price at exit
    contracts = Column(Integer, nullable=True)            # number of contracts
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DailyPerformance(Base):
    __tablename__ = "daily_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)
    starting_capital = Column(Float, nullable=False)
    ending_capital = Column(Float, nullable=False)
    realized_pnl = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    regime = Column(String, nullable=True)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    equity = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    buying_power = Column(Float, nullable=False)
    peak_equity = Column(Float, nullable=False)
    drawdown_pct = Column(Float, default=0.0)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    symbol = Column(String, default="SPY")
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    interval = Column(String, default="1m")
    initial_capital = Column(Float, default=25000.0)
    strategies = Column(String, nullable=False)  # comma-separated
    total_return_pct = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    avg_win = Column(Float, nullable=True)
    avg_loss = Column(Float, nullable=True)
    equity_curve = Column(JSON, nullable=True)
    trades_json = Column(JSON, nullable=True)


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    enabled = Column(Boolean, default=True)
    params = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class StrategyRanking(Base):
    __tablename__ = "strategy_rankings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String, nullable=False, unique=True)

    # Short-term (1d/5d/30d intraday) metrics
    avg_sharpe_ratio = Column(Float, default=0.0)
    avg_profit_factor = Column(Float, default=0.0)
    avg_win_rate = Column(Float, default=0.0)
    avg_return_pct = Column(Float, default=0.0)
    avg_max_drawdown_pct = Column(Float, default=0.0)
    st_composite_score = Column(Float, default=0.0)   # pure short-term score
    total_backtest_trades = Column(Integer, default=0)
    backtest_count = Column(Integer, default=0)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Long-term (10-15Y daily) metrics — nullable until first LT run
    lt_cagr_pct = Column(Float, nullable=True)
    lt_sharpe = Column(Float, nullable=True)
    lt_sortino = Column(Float, nullable=True)
    lt_calmar = Column(Float, nullable=True)
    lt_max_drawdown_pct = Column(Float, nullable=True)
    lt_win_rate = Column(Float, nullable=True)
    lt_profit_factor = Column(Float, nullable=True)
    lt_total_trades = Column(Integer, nullable=True)
    lt_years_tested = Column(Float, nullable=True)
    lt_composite_score = Column(Float, nullable=True)  # pure long-term score
    lt_computed_at = Column(DateTime, nullable=True)

    # Blended composite (55% ST + 45% LT when LT data exists, else 100% ST)
    composite_score = Column(Float, default=0.0)


class StrategyLivePerformance(Base):
    """Tracks real-time paper/live trade results per strategy for adaptive blending with backtest scores."""
    __tablename__ = "strategy_live_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String, unique=True, nullable=False)
    live_trades = Column(Integer, default=0)
    live_wins = Column(Integer, default=0)
    live_losses = Column(Integer, default=0)
    live_pnl_total = Column(Float, default=0.0)
    live_win_rate = Column(Float, default=0.0)
    live_avg_win = Column(Float, default=0.0)
    live_avg_loss = Column(Float, default=0.0)
    live_profit_factor = Column(Float, default=0.0)
    consecutive_live_losses = Column(Integer, default=0)
    auto_disabled = Column(Boolean, default=False)
    disabled_reason = Column(String, nullable=True)
    disabled_at = Column(DateTime, nullable=True)
    last_trade_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TradingConfig(Base):
    """Single-row table persisting trading settings across restarts."""
    __tablename__ = "trading_config"

    id = Column(Integer, primary_key=True, default=1)
    initial_capital = Column(Float, default=25000.0)
    max_risk_per_trade = Column(Float, default=0.015)
    daily_loss_limit = Column(Float, default=0.02)
    max_drawdown = Column(Float, default=0.16)
    max_position_pct = Column(Float, default=0.30)
    max_trades_per_day = Column(Integer, default=10)
    cooldown_after_consecutive_losses = Column(Integer, default=3)
    cooldown_minutes = Column(Integer, default=15)
    min_signal_confidence = Column(Float, default=0.6)
    # Options config
    default_spread_width = Column(Float, default=3.0)
    preferred_dte_min = Column(Integer, default=5)
    preferred_dte_max = Column(Integer, default=14)
    target_delta_short = Column(Float, default=0.20)
    credit_profit_target_pct = Column(Float, default=0.50)
    max_contracts_per_trade = Column(Integer, default=10)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
