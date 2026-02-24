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
    initial_capital: float = 25000.0
    max_risk_per_trade: float = 0.01
    daily_loss_limit: float = 0.02
    max_drawdown: float = 0.16
    max_position_pct: float = 0.30
    max_trades_per_day: int = 100
    cooldown_after_consecutive_losses: int = 2
    cooldown_minutes: int = 30
    min_signal_confidence: float = 0.65

    # Options settings
    default_spread_width: float = 3.0
    preferred_dte_min: int = 5
    preferred_dte_max: int = 14
    target_delta_short: float = 0.20
    credit_profit_target_pct: float = 0.50
    max_contracts_per_trade: int = 3
    options_commission_per_contract: float = 0.65
    # Trailing stop: activate after this fraction of premium gained, then trail by trail_pct
    trailing_stop_trigger_pct: float = 0.25   # activate after 25% gain on debit / 25% premium decay on credit
    trailing_stop_trail_pct: float = 0.20     # close if premium reverses 20% from best level

    # Database
    database_url: str = "sqlite+aiosqlite:///./spy_daytrader.db"

    # Long-term backtest cache
    data_cache_dir: str = "./data_cache"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
