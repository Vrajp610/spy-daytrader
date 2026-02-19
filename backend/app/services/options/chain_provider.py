"""Options chain data providers: Schwab, Yahoo, Synthetic (paper mode)."""

from __future__ import annotations
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.options.models import (
    OptionType, OptionAction, OptionLeg, OptionChainSnapshot,
)
from app.services.options import pricing

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class SchwabChainProvider:
    """Fetch option chain from Charles Schwab API."""

    async def get_chain(
        self, symbol: str, dte_min: int = 5, dte_max: int = 14,
    ) -> Optional[OptionChainSnapshot]:
        try:
            from app.services.schwab_client import schwab_client
            if not schwab_client._client:
                return None

            resp = schwab_client._client.get_option_chain(
                symbol,
                contract_type=None,  # ALL
                strike_count=30,
                from_date=datetime.now(ET) + timedelta(days=max(1, dte_min)),
                to_date=datetime.now(ET) + timedelta(days=dte_max),
            )
            if resp.status_code != 200:
                logger.warning(f"Schwab chain request failed: {resp.status_code}")
                return None

            data = resp.json()
            return self._parse_schwab_chain(data, symbol)
        except ImportError:
            return None
        except Exception as e:
            logger.error(f"Schwab chain error: {e}")
            return None

    def _parse_schwab_chain(self, data: dict, symbol: str) -> OptionChainSnapshot:
        underlying_price = data.get("underlyingPrice", 0.0)
        calls = {}
        puts = {}
        expirations = set()

        for exp_date, strikes_map in data.get("callExpDateMap", {}).items():
            exp = exp_date.split(":")[0]  # "2025-03-07:5" -> "2025-03-07"
            expirations.add(exp)
            for strike_str, contracts in strikes_map.items():
                strike = float(strike_str)
                if contracts:
                    c = contracts[0]
                    leg = OptionLeg(
                        contract_symbol=c.get("symbol", ""),
                        option_type=OptionType.CALL,
                        strike=strike,
                        expiration=exp,
                        action=OptionAction.BUY_TO_OPEN,
                        quantity=1,
                        premium=(c.get("bid", 0) + c.get("ask", 0)) / 2,
                        delta=c.get("delta", 0.0),
                        gamma=c.get("gamma", 0.0),
                        theta=c.get("theta", 0.0),
                        vega=c.get("vega", 0.0),
                        iv=c.get("volatility", 0.0) / 100.0,
                    )
                    calls[(exp, strike)] = leg

        for exp_date, strikes_map in data.get("putExpDateMap", {}).items():
            exp = exp_date.split(":")[0]
            expirations.add(exp)
            for strike_str, contracts in strikes_map.items():
                strike = float(strike_str)
                if contracts:
                    c = contracts[0]
                    leg = OptionLeg(
                        contract_symbol=c.get("symbol", ""),
                        option_type=OptionType.PUT,
                        strike=strike,
                        expiration=exp,
                        action=OptionAction.BUY_TO_OPEN,
                        quantity=1,
                        premium=(c.get("bid", 0) + c.get("ask", 0)) / 2,
                        delta=c.get("delta", 0.0),
                        gamma=c.get("gamma", 0.0),
                        theta=c.get("theta", 0.0),
                        vega=c.get("vega", 0.0),
                        iv=c.get("volatility", 0.0) / 100.0,
                    )
                    puts[(exp, strike)] = leg

        return OptionChainSnapshot(
            underlying_price=underlying_price,
            timestamp=datetime.now(ET),
            expirations=sorted(expirations),
            calls=calls,
            puts=puts,
        )


class YahooChainProvider:
    """Fetch option chain from Yahoo Finance as fallback."""

    async def get_chain(
        self, symbol: str, dte_min: int = 5, dte_max: int = 14,
    ) -> Optional[OptionChainSnapshot]:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._fetch_sync, symbol, dte_min, dte_max)
        except Exception as e:
            logger.error(f"Yahoo chain error: {e}")
            return None

    def _fetch_sync(self, symbol: str, dte_min: int, dte_max: int) -> Optional[OptionChainSnapshot]:
        try:
            import requests
            # Get available expirations
            url = f"https://query2.finance.yahoo.com/v7/finance/options/{symbol}"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None

            data = resp.json()
            result = data.get("optionChain", {}).get("result", [])
            if not result:
                return None

            quote = result[0].get("quote", {})
            underlying_price = quote.get("regularMarketPrice", 0.0)
            available_exps = result[0].get("expirationDates", [])

            today = datetime.now(ET).date()
            target_min = today + timedelta(days=max(1, dte_min))
            target_max = today + timedelta(days=dte_max)

            calls = {}
            puts = {}
            expirations = []

            for exp_ts in available_exps:
                exp_dt = datetime.fromtimestamp(exp_ts)
                exp_d = exp_dt.date()
                if target_min <= exp_d <= target_max:
                    exp_str = exp_d.strftime("%Y-%m-%d")
                    dte = (exp_d - today).days
                    expirations.append(exp_str)

                    # Fetch chain for this expiration
                    chain_url = f"{url}?date={exp_ts}"
                    chain_resp = requests.get(chain_url, headers=headers, timeout=10)
                    if chain_resp.status_code != 200:
                        continue

                    chain_data = chain_resp.json()
                    chain_result = chain_data.get("optionChain", {}).get("result", [])
                    if not chain_result:
                        continue

                    options = chain_result[0].get("options", [])
                    if not options:
                        continue

                    T = max(0.001, dte / 365.0)

                    for c in options[0].get("calls", []):
                        strike = c.get("strike", 0)
                        iv_val = c.get("impliedVolatility", 0.3)
                        d = pricing.delta(underlying_price, strike, T, 0.05, iv_val, OptionType.CALL)
                        g = pricing.gamma(underlying_price, strike, T, 0.05, iv_val)
                        th = pricing.theta(underlying_price, strike, T, 0.05, iv_val, OptionType.CALL)
                        v = pricing.vega(underlying_price, strike, T, 0.05, iv_val)

                        leg = OptionLeg(
                            contract_symbol=c.get("contractSymbol", ""),
                            option_type=OptionType.CALL,
                            strike=strike,
                            expiration=exp_str,
                            action=OptionAction.BUY_TO_OPEN,
                            quantity=1,
                            premium=(c.get("bid", 0) + c.get("ask", 0)) / 2,
                            delta=d, gamma=g, theta=th, vega=v, iv=iv_val,
                        )
                        calls[(exp_str, strike)] = leg

                    for p_data in options[0].get("puts", []):
                        strike = p_data.get("strike", 0)
                        iv_val = p_data.get("impliedVolatility", 0.3)
                        d = pricing.delta(underlying_price, strike, T, 0.05, iv_val, OptionType.PUT)
                        g = pricing.gamma(underlying_price, strike, T, 0.05, iv_val)
                        th = pricing.theta(underlying_price, strike, T, 0.05, iv_val, OptionType.PUT)
                        v = pricing.vega(underlying_price, strike, T, 0.05, iv_val)

                        leg = OptionLeg(
                            contract_symbol=p_data.get("contractSymbol", ""),
                            option_type=OptionType.PUT,
                            strike=strike,
                            expiration=exp_str,
                            action=OptionAction.BUY_TO_OPEN,
                            quantity=1,
                            premium=(p_data.get("bid", 0) + p_data.get("ask", 0)) / 2,
                            delta=d, gamma=g, theta=th, vega=v, iv=iv_val,
                        )
                        puts[(exp_str, strike)] = leg

            if not expirations:
                return None

            return OptionChainSnapshot(
                underlying_price=underlying_price,
                timestamp=datetime.now(ET),
                expirations=sorted(expirations),
                calls=calls,
                puts=puts,
            )
        except Exception as e:
            logger.error(f"Yahoo chain fetch error: {e}")
            return None


class SyntheticChainProvider:
    """Generate synthetic option chain using Black-Scholes for paper mode."""

    def generate(
        self, underlying_price: float, atr: float = 2.0,
        dte_min: int = 5, dte_max: int = 14,
    ) -> OptionChainSnapshot:
        iv = pricing.iv_from_atr(atr, underlying_price)
        r = 0.05  # risk-free rate
        now = datetime.now(ET)
        today = now.date()

        # Generate weekly expirations (Fridays) within DTE range
        # Ensure minimum 1 DTE to never pick today or past dates
        min_offset = max(1, dte_min)
        expirations = []
        for day_offset in range(min_offset, dte_max + 1):
            exp_date = today + timedelta(days=day_offset)
            if exp_date.weekday() == 4:  # Friday
                expirations.append(exp_date.strftime("%Y-%m-%d"))

        # If no Friday falls in range, use the closest one after min_offset
        if not expirations:
            for day_offset in range(min_offset, dte_max + 7):
                exp_date = today + timedelta(days=day_offset)
                if exp_date.weekday() == 4:
                    expirations.append(exp_date.strftime("%Y-%m-%d"))
                    break

        calls = {}
        puts = {}

        # Generate strikes at $1 intervals, ±$15 from ATM
        atm = round(underlying_price)
        strikes = [atm + i for i in range(-15, 16)]

        for exp_str in expirations:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            T = max(0.001, dte / 365.0)

            for strike in strikes:
                # Generate OCC-style symbol
                exp_compact = exp_date.strftime("%y%m%d")
                strike_int = int(strike * 1000)

                # Call
                call_symbol = f"SPY{exp_compact}C{strike_int:08d}"
                call_price = pricing.black_scholes_price(underlying_price, strike, T, r, iv, OptionType.CALL)
                call_delta = pricing.delta(underlying_price, strike, T, r, iv, OptionType.CALL)
                call_gamma = pricing.gamma(underlying_price, strike, T, r, iv)
                call_theta = pricing.theta(underlying_price, strike, T, r, iv, OptionType.CALL)
                call_vega = pricing.vega(underlying_price, strike, T, r, iv)

                # Add bid-ask spread simulation (5-10% of premium)
                spread_pct = 0.07
                mid = max(0.01, call_price)

                calls[(exp_str, float(strike))] = OptionLeg(
                    contract_symbol=call_symbol,
                    option_type=OptionType.CALL,
                    strike=float(strike),
                    expiration=exp_str,
                    action=OptionAction.BUY_TO_OPEN,
                    quantity=1,
                    premium=round(mid, 2),
                    delta=round(call_delta, 4),
                    gamma=round(call_gamma, 6),
                    theta=round(call_theta, 4),
                    vega=round(call_vega, 4),
                    iv=round(iv, 4),
                )

                # Put
                put_symbol = f"SPY{exp_compact}P{strike_int:08d}"
                put_price = pricing.black_scholes_price(underlying_price, strike, T, r, iv, OptionType.PUT)
                put_delta = pricing.delta(underlying_price, strike, T, r, iv, OptionType.PUT)
                put_theta = pricing.theta(underlying_price, strike, T, r, iv, OptionType.PUT)

                mid = max(0.01, put_price)

                puts[(exp_str, float(strike))] = OptionLeg(
                    contract_symbol=put_symbol,
                    option_type=OptionType.PUT,
                    strike=float(strike),
                    expiration=exp_str,
                    action=OptionAction.BUY_TO_OPEN,
                    quantity=1,
                    premium=round(mid, 2),
                    delta=round(put_delta, 4),
                    gamma=round(call_gamma, 6),  # same gamma for calls/puts
                    theta=round(put_theta, 4),
                    vega=round(call_vega, 4),     # same vega for calls/puts
                    iv=round(iv, 4),
                )

        return OptionChainSnapshot(
            underlying_price=underlying_price,
            timestamp=now,
            expirations=expirations,
            calls=calls,
            puts=puts,
            iv_rank=50.0,
            iv_percentile=50.0,
        )


class OptionChainProvider:
    """Composite chain provider: Schwab -> Yahoo -> Synthetic fallback.

    Caches results for 60 seconds.
    """

    def __init__(self):
        self._schwab = SchwabChainProvider()
        self._yahoo = YahooChainProvider()
        self._synthetic = SyntheticChainProvider()
        self._cache: Optional[OptionChainSnapshot] = None
        self._cache_time: float = 0.0
        self._cache_ttl: float = 60.0  # seconds

    async def get_chain(
        self, symbol: str = "SPY",
        underlying_price: float = 0.0, atr: float = 2.0,
    ) -> OptionChainSnapshot:
        """Get option chain, trying Schwab -> Yahoo -> Synthetic."""
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        dte_min = settings.preferred_dte_min
        dte_max = settings.preferred_dte_max

        # Try Schwab
        chain = await self._schwab.get_chain(symbol, dte_min, dte_max)
        if chain and chain.calls:
            logger.info(f"Fetched chain from Schwab: {len(chain.calls)} calls, {len(chain.puts)} puts")
            self._cache = chain
            self._cache_time = now
            return chain

        # Try Yahoo
        chain = await self._yahoo.get_chain(symbol, dte_min, dte_max)
        if chain and chain.calls:
            logger.info(f"Fetched chain from Yahoo: {len(chain.calls)} calls, {len(chain.puts)} puts")
            self._cache = chain
            self._cache_time = now
            return chain

        # Synthetic fallback
        if underlying_price <= 0:
            underlying_price = 590.0  # reasonable SPY default
        chain = self._synthetic.generate(underlying_price, atr, dte_min, dte_max)
        logger.info(f"Generated synthetic chain: {len(chain.calls)} calls, {len(chain.puts)} puts, IV={chain.calls[list(chain.calls.keys())[0]].iv:.1%}" if chain.calls else "Generated empty synthetic chain")
        self._cache = chain
        self._cache_time = now
        return chain
