"""Charles Schwab API client wrapper using schwab-py."""

from __future__ import annotations
import json
import logging
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class SchwabClient:
    """Wrapper around schwab-py for OAuth2, quotes, orders, and streaming."""

    def __init__(self):
        self._client = None
        self._stream_client = None
        self._account_hash = settings.schwab_account_hash

    @property
    def is_configured(self) -> bool:
        return bool(settings.schwab_app_key and settings.schwab_app_secret)

    async def initialize(self):
        """Initialize the Schwab API client with token-based auth."""
        if not self.is_configured:
            logger.warning("Schwab API not configured - running in paper-only mode")
            return

        try:
            import schwab
        except ImportError:
            logger.warning("schwab-py not installed - running in paper-only mode")
            return

        try:
            import schwab
            token_path = Path(settings.schwab_token_path)
            if token_path.exists():
                self._client = schwab.auth.client_from_token_file(
                    token_path=str(token_path),
                    api_key=settings.schwab_app_key,
                    app_secret=settings.schwab_app_secret,
                )
                logger.info("Schwab client initialized from token file")
            else:
                logger.warning(
                    f"Token file not found at {token_path}. "
                    "Run schwab-py auth flow to generate token."
                )
        except Exception as e:
            logger.error(f"Failed to initialize Schwab client: {e}")

    async def get_quote(self, symbol: str = "SPY") -> Optional[dict]:
        if not self._client:
            return None
        try:
            resp = self._client.get_quote(symbol)
            if resp.status_code == 200:
                data = resp.json()
                quote = data.get(symbol, {}).get("quote", {})
                return {
                    "symbol": symbol,
                    "last": quote.get("lastPrice"),
                    "bid": quote.get("bidPrice"),
                    "ask": quote.get("askPrice"),
                    "volume": quote.get("totalVolume"),
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as e:
            logger.error(f"Error getting quote: {e}")
        return None

    async def get_account_info(self) -> Optional[dict]:
        if not self._client or not self._account_hash:
            return None
        try:
            resp = self._client.get_account(
                self._account_hash,
                fields=["positions"],
            )
            if resp.status_code == 200:
                data = resp.json()
                acct = data.get("securitiesAccount", {})
                balances = acct.get("currentBalances", {})
                return {
                    "equity": balances.get("liquidationValue", 0),
                    "cash": balances.get("cashBalance", 0),
                    "buying_power": balances.get("buyingPower", 0),
                    "positions": acct.get("positions", []),
                }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
        return None

    async def place_order(
        self,
        symbol: str,
        quantity: int,
        side: str,  # "BUY" or "SELL"
        order_type: str = "MARKET",
        price: Optional[float] = None,
    ) -> Optional[dict]:
        if not self._client or not self._account_hash:
            return None
        try:
            from schwab.orders.equities import equity_buy_market, equity_sell_market

            if side == "BUY":
                order = equity_buy_market(symbol, quantity)
            else:
                order = equity_sell_market(symbol, quantity)

            resp = self._client.place_order(self._account_hash, order)
            if resp.status_code in (200, 201):
                order_id = resp.headers.get("Location", "").split("/")[-1]
                return {"order_id": order_id, "status": "FILLED"}
            else:
                logger.error(f"Order failed: {resp.status_code} {resp.text}")
                return {"order_id": None, "status": "FAILED", "error": resp.text}
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None

    async def get_price_history(
        self,
        symbol: str = "SPY",
        period_type: str = "day",
        period: int = 1,
        frequency_type: str = "minute",
        frequency: int = 1,
    ) -> Optional[list]:
        if not self._client:
            return None
        try:
            from schwab.client import Client
            resp = self._client.get_price_history_every_minute(
                symbol,
                period_type=Client.PriceHistory.PeriodType.DAY,
                period=Client.PriceHistory.Period.ONE_DAY,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("candles", [])
        except Exception as e:
            logger.error(f"Error getting price history: {e}")
        return None


schwab_client = SchwabClient()
