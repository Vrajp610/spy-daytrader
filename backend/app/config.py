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
    max_risk_per_trade: float = 0.015
    daily_loss_limit: float = 0.02
    max_drawdown: float = 0.16
    max_position_pct: float = 0.30
    max_trades_per_day: int = 10
    cooldown_after_consecutive_losses: int = 3
    cooldown_minutes: int = 15
    min_signal_confidence: float = 0.6

    # Database
    database_url: str = "sqlite+aiosqlite:///./spy_daytrader.db"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
