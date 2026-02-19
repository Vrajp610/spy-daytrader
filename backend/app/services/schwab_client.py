"""Charles Schwab API client wrapper using schwab-py."""

from __future__ import annotations
import json
import logging
from typing import Optional
from datetime import datetime, timedelta, timezone
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
                    "timestamp": datetime.now(timezone.utc).isoformat(),
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

    async def place_bracket_order(
        self,
        symbol: str,
        quantity: int,
        side: str,  # "BUY" or "SELL"
        stop_loss: float,
        take_profit: float,
        trailing_stop_pct: Optional[float] = None,
    ) -> Optional[dict]:
        """Place a bracket order (OTO): market entry + stop loss + take profit.

        The broker enforces the stops even if the bot goes offline.
        """
        if not self._client or not self._account_hash:
            return None
        try:
            from schwab.orders.equities import equity_buy_market, equity_sell_market
            from schwab.orders.common import OrderType, Session, Duration, one_cancels_other

            # Primary leg: market order
            if side == "BUY":
                primary = equity_buy_market(symbol, quantity)
                # Exit side for children
                exit_side_stop = equity_sell_market(symbol, quantity)
                exit_side_tp = equity_sell_market(symbol, quantity)
            else:
                primary = equity_sell_market(symbol, quantity)
                exit_side_stop = equity_buy_market(symbol, quantity)
                exit_side_tp = equity_buy_market(symbol, quantity)

            # Build stop loss child order
            from schwab.orders.equities import equity_buy_limit, equity_sell_limit
            from schwab.orders.common import StopPriceLinkBasis, StopPriceLinkType

            # Stop loss order
            stop_order_spec = {
                "orderType": "STOP",
                "session": "NORMAL",
                "duration": "DAY",
                "stopPrice": str(round(stop_loss, 2)),
                "orderLegCollection": [{
                    "instruction": "SELL" if side == "BUY" else "BUY",
                    "quantity": quantity,
                    "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                }],
            }

            # Take profit order (limit)
            tp_order_spec = {
                "orderType": "LIMIT",
                "session": "NORMAL",
                "duration": "DAY",
                "price": str(round(take_profit, 2)),
                "orderLegCollection": [{
                    "instruction": "SELL" if side == "BUY" else "BUY",
                    "quantity": quantity,
                    "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                }],
            }

            # Build OTO bracket: primary triggers OCO(stop, target)
            bracket_spec = {
                "orderType": "MARKET",
                "session": "NORMAL",
                "duration": "DAY",
                "orderStrategyType": "TRIGGER",
                "orderLegCollection": [{
                    "instruction": side,
                    "quantity": quantity,
                    "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                }],
                "childOrderStrategies": [{
                    "orderStrategyType": "OCO",
                    "childOrderStrategies": [stop_order_spec, tp_order_spec],
                }],
            }

            resp = self._client.place_order(self._account_hash, bracket_spec)
            if resp.status_code in (200, 201):
                order_id = resp.headers.get("Location", "").split("/")[-1]
                logger.info(f"Bracket order placed: {order_id} ({side} {quantity} {symbol} "
                            f"SL={stop_loss} TP={take_profit})")
                return {"order_id": order_id, "status": "FILLED"}
            else:
                logger.error(f"Bracket order failed: {resp.status_code} {resp.text}")
                return {"order_id": None, "status": "FAILED", "error": resp.text}
        except Exception as e:
            logger.error(f"Error placing bracket order: {e}")
            return None

    async def place_trailing_stop(
        self,
        symbol: str,
        quantity: int,
        side: str,  # "SELL" to protect long, "BUY" to protect short
        trail_pct: float,
    ) -> Optional[dict]:
        """Place a trailing stop order (percentage-based)."""
        if not self._client or not self._account_hash:
            return None
        try:
            trailing_spec = {
                "orderType": "TRAILING_STOP",
                "session": "NORMAL",
                "duration": "DAY",
                "stopPriceLinkBasis": "LAST",
                "stopPriceLinkType": "PERCENT",
                "stopPriceOffset": str(round(trail_pct, 2)),
                "orderLegCollection": [{
                    "instruction": side,
                    "quantity": quantity,
                    "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                }],
            }

            resp = self._client.place_order(self._account_hash, trailing_spec)
            if resp.status_code in (200, 201):
                order_id = resp.headers.get("Location", "").split("/")[-1]
                logger.info(f"Trailing stop placed: {order_id} ({side} {quantity} {symbol} trail={trail_pct}%)")
                return {"order_id": order_id, "status": "PLACED"}
            else:
                logger.error(f"Trailing stop failed: {resp.status_code} {resp.text}")
                return {"order_id": None, "status": "FAILED", "error": resp.text}
        except Exception as e:
            logger.error(f"Error placing trailing stop: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order by ID."""
        if not self._client or not self._account_hash:
            return False
        try:
            resp = self._client.cancel_order(order_id, self._account_hash)
            if resp.status_code in (200, 201):
                logger.info(f"Order {order_id} cancelled")
                return True
            else:
                logger.error(f"Cancel order failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    async def replace_order(self, order_id: str, new_order: dict) -> Optional[dict]:
        """Replace (modify) an existing order with a new order spec."""
        if not self._client or not self._account_hash:
            return None
        try:
            resp = self._client.replace_order(self._account_hash, order_id, new_order)
            if resp.status_code in (200, 201):
                new_id = resp.headers.get("Location", "").split("/")[-1]
                logger.info(f"Order {order_id} replaced with {new_id}")
                return {"order_id": new_id, "status": "REPLACED"}
            else:
                logger.error(f"Replace order failed: {resp.status_code} {resp.text}")
                return {"order_id": None, "status": "FAILED", "error": resp.text}
        except Exception as e:
            logger.error(f"Error replacing order: {e}")
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

    async def get_option_chain(
        self,
        symbol: str = "SPY",
        contract_type: Optional[str] = None,
        strike_count: int = 30,
        dte_range: Optional[tuple[int, int]] = None,
    ) -> Optional[dict]:
        """Fetch option chain from Schwab API."""
        if not self._client:
            return None
        try:
            kwargs = {"symbol": symbol, "strike_count": strike_count}
            if dte_range:
                from_date = datetime.now() + timedelta(days=dte_range[0])
                to_date = datetime.now() + timedelta(days=dte_range[1])
                kwargs["from_date"] = from_date
                kwargs["to_date"] = to_date
            resp = self._client.get_option_chain(**kwargs)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"Error getting option chain: {e}")
        return None

    async def place_options_order(self, order) -> Optional[dict]:
        """Place an options order based on OptionsOrder object.

        Dispatches to the appropriate schwab-py order builder.
        """
        if not self._client or not self._account_hash:
            return None

        try:
            from app.services.options.models import OptionsStrategyType, OptionAction

            strategy_type = order.strategy_type
            legs = order.legs

            # Build order spec based on strategy type
            order_spec = self._build_options_order_spec(order)
            if order_spec is None:
                return None

            resp = self._client.place_order(self._account_hash, order_spec)
            if resp.status_code in (200, 201):
                order_id = resp.headers.get("Location", "").split("/")[-1]
                logger.info(f"Options order placed: {order_id} ({order.to_display_string()})")
                return {"order_id": order_id, "status": "FILLED"}
            else:
                logger.error(f"Options order failed: {resp.status_code} {resp.text}")
                return {"order_id": None, "status": "FAILED", "error": resp.text}
        except Exception as e:
            logger.error(f"Error placing options order: {e}")
            return None

    def _build_options_order_spec(self, order) -> Optional[dict]:
        """Build Schwab order spec for an options order."""
        from app.services.options.models import OptionAction

        legs_spec = []
        for leg in order.legs:
            instruction = "SELL_TO_OPEN" if leg.action == OptionAction.SELL_TO_OPEN else "BUY_TO_OPEN"
            legs_spec.append({
                "instruction": instruction,
                "quantity": leg.quantity,
                "instrument": {
                    "symbol": leg.contract_symbol,
                    "assetType": "OPTION",
                },
            })

        price = abs(order.net_premium)

        return {
            "orderType": "NET_CREDIT" if order.is_credit else "NET_DEBIT",
            "session": "NORMAL",
            "duration": "DAY",
            "price": str(round(price, 2)),
            "complexOrderStrategyType": "VERTICAL" if len(order.legs) == 2 else "IRON_CONDOR" if len(order.legs) == 4 else "SINGLE",
            "orderLegCollection": legs_spec,
        }

    async def close_options_position(self, order) -> Optional[dict]:
        """Close an options position by reversing all legs."""
        if not self._client or not self._account_hash:
            return None

        try:
            from app.services.options.models import OptionAction

            legs_spec = []
            for leg in order.legs:
                # Reverse the action
                if leg.action == OptionAction.SELL_TO_OPEN:
                    instruction = "BUY_TO_CLOSE"
                else:
                    instruction = "SELL_TO_CLOSE"
                legs_spec.append({
                    "instruction": instruction,
                    "quantity": leg.quantity,
                    "instrument": {
                        "symbol": leg.contract_symbol,
                        "assetType": "OPTION",
                    },
                })

            close_spec = {
                "orderType": "MARKET",
                "session": "NORMAL",
                "duration": "DAY",
                "complexOrderStrategyType": "VERTICAL" if len(order.legs) == 2 else "IRON_CONDOR" if len(order.legs) == 4 else "SINGLE",
                "orderLegCollection": legs_spec,
            }

            resp = self._client.place_order(self._account_hash, close_spec)
            if resp.status_code in (200, 201):
                order_id = resp.headers.get("Location", "").split("/")[-1]
                logger.info(f"Options close order placed: {order_id}")
                return {"order_id": order_id, "status": "FILLED"}
            else:
                logger.error(f"Options close failed: {resp.status_code} {resp.text}")
                return None
        except Exception as e:
            logger.error(f"Error closing options position: {e}")
            return None


schwab_client = SchwabClient()
