"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Schwab API
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_callback_url: str = "https://127.0.0.1:8182/callback"
    schwab_token_path: str = "./schwab_token.json"
    schwab_account_hash: str = ""

    # Trading
    trading_mode: str = Field(default="paper", pattern="^(paper|live)$")
    initial_capital: float = 50000.0           # $50k base capital
    max_risk_per_trade: float = 0.015          # 1.5% per trade ($750 at $50k)
    daily_loss_limit: float = 0.02             # 2% daily loss gate ($1k at $50k)
    max_drawdown: float = 0.16                 # 16% circuit breaker ($8k at $50k)
    max_position_pct: float = 0.20            # single position cap: 20% of equity ($10k)
    max_trades_per_day: int = 100
    cooldown_after_consecutive_losses: int = 2
    cooldown_minutes: int = 30
    min_signal_confidence: float = 0.60

    # Options settings
    default_spread_width: float = 5.0          # wider spread = better R:R for debit spreads
    preferred_dte_min: int = 1                 # allow 0-2 DTE for scalp strategies
    preferred_dte_max: int = 14
    target_delta_short: float = 0.20
    credit_profit_target_pct: float = 0.50
    max_contracts_per_trade: int = 5           # scaled to $50k: 5 contracts default
    max_contracts_per_trade_theta: int = 50    # higher ceiling for theta-decay sizing
    weekly_credit_target: float = 2000.0       # target weekly credit collection ($)
    options_commission_per_contract: float = 0.65
    # Trailing stop: activate after this fraction of premium gained, then trail by trail_pct
    trailing_stop_trigger_pct: float = 0.25   # activate after 25% gain on debit / 25% premium decay on credit
    trailing_stop_trail_pct: float = 0.20     # close if premium reverses 20% from best level

    # Delta-adjusted sizing (new strategies: orb_scalp, wtr_long_call, vol_spike)
    # Scale down positions when ATR > this threshold (% of underlying price)
    high_atr_threshold: float = 3.0           # ATR > $3 → 50% size reduction
    # Scale down when IV rank is in the top percentile of 30-day range
    high_iv_rank_threshold: float = 80.0      # IV rank > 80 → 50% size reduction for debit buyers
    # Portfolio delta exposure cap: max |delta| = 10% of equity / underlying price
    max_portfolio_delta_pct: float = 0.10     # 10% of equity in delta-adjusted notional

    # AI intelligence layer (Claude Haiku for news + adversarial trade advisor)
    # Set via ANTHROPIC_API_KEY environment variable or .env file.
    # If not set, news scanner falls back to keyword scoring and trade advisor
    # always returns PROCEED (both are non-blocking fallbacks).
    anthropic_api_key: str = ""

    # TradingView webhook secret — must match the {{secret}} placeholder in Pine Script alerts.
    # Set via TRADINGVIEW_WEBHOOK_SECRET env var or .env file.
    # Leave empty to disable secret verification (not recommended in production).
    tradingview_webhook_secret: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./spy_daytrader.db"

    # Long-term backtest cache
    data_cache_dir: str = "./data_cache"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
