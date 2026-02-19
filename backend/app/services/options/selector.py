"""Options selector: maps TradeSignal + MarketRegime + OptionChain -> OptionsOrder."""

from __future__ import annotations
import logging
from typing import Optional

from app.config import settings
from app.services.options.models import (
    OptionType, OptionAction, OptionLeg, OptionsOrder,
    OptionsStrategyType, OptionChainSnapshot, OPTIONS_EXIT_RULES,
)
from app.services.strategies.base import TradeSignal, Direction
from app.services.strategies.regime_detector import MarketRegime

logger = logging.getLogger(__name__)


class OptionsSelector:
    """Maps directional signals + regime to specific options contracts."""

    def select(
        self,
        signal: TradeSignal,
        regime: MarketRegime,
        chain: OptionChainSnapshot,
        capital: float,
        risk_fraction: float,
    ) -> Optional[OptionsOrder]:
        """Select the best options structure for the given signal and regime.

        Returns an OptionsOrder with specific legs, or None if no suitable trade.
        """
        confidence = signal.confidence
        options_pref = signal.metadata.get("options_preference")

        # Determine strategy type based on confidence + regime + preference + IV
        iv_rank = chain.iv_rank if chain.iv_rank is not None else 50.0
        strategy_type = self._select_strategy_type(
            signal.direction, confidence, regime, options_pref, iv_rank,
        )
        if strategy_type is None:
            return None

        # Pick expiration
        expiration = self._select_expiration(chain, strategy_type)
        if not expiration:
            logger.warning("No suitable expiration found")
            return None

        # Build the order
        order = self._build_order(
            strategy_type, signal, chain, expiration, capital, risk_fraction,
        )
        return order

    def _select_strategy_type(
        self,
        direction: Direction,
        confidence: float,
        regime: MarketRegime,
        preference: Optional[str],
        iv_rank: float = 50.0,
    ) -> Optional[OptionsStrategyType]:
        """Select options strategy type using preference, IV rank, regime, and confidence."""

        if confidence < 0.55:
            return None

        # Map preference string to strategy type
        pref_map = {
            "credit_spread": (
                OptionsStrategyType.PUT_CREDIT_SPREAD if direction == Direction.LONG
                else OptionsStrategyType.CALL_CREDIT_SPREAD
            ),
            "debit_spread": (
                OptionsStrategyType.CALL_DEBIT_SPREAD if direction == Direction.LONG
                else OptionsStrategyType.PUT_DEBIT_SPREAD
            ),
            "iron_condor": OptionsStrategyType.IRON_CONDOR,
            "straddle": OptionsStrategyType.LONG_STRADDLE,
            "strangle": OptionsStrategyType.LONG_STRANGLE,
            "long_call": OptionsStrategyType.LONG_CALL,
            "long_put": OptionsStrategyType.LONG_PUT,
        }

        # IV rank adjustments - override preference when IV conditions are extreme
        if iv_rank > 70 and preference in ("debit_spread", "straddle", "strangle", "long_call", "long_put"):
            # High IV: don't buy expensive premium, sell it instead
            if regime == MarketRegime.RANGE_BOUND:
                return OptionsStrategyType.IRON_CONDOR
            if direction == Direction.LONG:
                return OptionsStrategyType.PUT_CREDIT_SPREAD
            return OptionsStrategyType.CALL_CREDIT_SPREAD

        if iv_rank < 25 and preference in ("credit_spread", "iron_condor"):
            # Low IV: premium too cheap to sell, buy it instead
            if regime == MarketRegime.VOLATILE:
                return OptionsStrategyType.LONG_STRADDLE if confidence >= 0.75 else OptionsStrategyType.LONG_STRANGLE
            if direction == Direction.LONG:
                return OptionsStrategyType.CALL_DEBIT_SPREAD
            return OptionsStrategyType.PUT_DEBIT_SPREAD

        # Honor strategy preference when available
        if preference and preference in pref_map:
            selected = pref_map[preference]
            return selected

        # Fallback: regime + confidence based selection
        if regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            if confidence >= 0.75:
                return OptionsStrategyType.PUT_CREDIT_SPREAD if direction == Direction.LONG else OptionsStrategyType.CALL_CREDIT_SPREAD
            if confidence >= 0.65:
                return OptionsStrategyType.CALL_DEBIT_SPREAD if direction == Direction.LONG else OptionsStrategyType.PUT_DEBIT_SPREAD
            # Lower confidence trending: naked long option for max leverage
            return OptionsStrategyType.LONG_CALL if direction == Direction.LONG else OptionsStrategyType.LONG_PUT

        if regime == MarketRegime.RANGE_BOUND:
            if iv_rank > 40:
                return OptionsStrategyType.IRON_CONDOR
            # Low IV range-bound: credit spread one side
            return OptionsStrategyType.PUT_CREDIT_SPREAD if direction == Direction.LONG else OptionsStrategyType.CALL_CREDIT_SPREAD

        if regime == MarketRegime.VOLATILE:
            if confidence >= 0.75:
                return OptionsStrategyType.LONG_STRADDLE
            if confidence >= 0.65:
                return OptionsStrategyType.LONG_STRANGLE
            # Lower confidence volatile: directional debit spread
            return OptionsStrategyType.CALL_DEBIT_SPREAD if direction == Direction.LONG else OptionsStrategyType.PUT_DEBIT_SPREAD

        # Absolute fallback
        if direction == Direction.LONG:
            return OptionsStrategyType.PUT_CREDIT_SPREAD
        return OptionsStrategyType.CALL_CREDIT_SPREAD

    def _select_expiration(
        self, chain: OptionChainSnapshot, strategy_type: OptionsStrategyType,
    ) -> Optional[str]:
        """Choose the best expiration date based on strategy type."""
        if not chain.expirations:
            return None

        from datetime import datetime, date, timedelta
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()

        # Credit spreads prefer 7-14 DTE, debit prefer 5-10 DTE
        is_credit = strategy_type in (
            OptionsStrategyType.PUT_CREDIT_SPREAD,
            OptionsStrategyType.CALL_CREDIT_SPREAD,
            OptionsStrategyType.IRON_CONDOR,
        )

        ideal_dte = 10 if is_credit else 7

        best_exp = None
        best_diff = float("inf")

        for exp_str in chain.expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte < max(1, settings.preferred_dte_min):
                    continue
                diff = abs(dte - ideal_dte)
                if diff < best_diff:
                    best_diff = diff
                    best_exp = exp_str
            except ValueError:
                continue

        return best_exp

    def _build_order(
        self,
        strategy_type: OptionsStrategyType,
        signal: TradeSignal,
        chain: OptionChainSnapshot,
        expiration: str,
        capital: float,
        risk_fraction: float,
    ) -> Optional[OptionsOrder]:
        """Build the complete OptionsOrder with legs."""
        price = chain.underlying_price
        spread_width = settings.default_spread_width
        target_delta = settings.target_delta_short

        if strategy_type == OptionsStrategyType.PUT_CREDIT_SPREAD:
            return self._build_put_credit_spread(
                chain, expiration, price, spread_width, target_delta,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.CALL_CREDIT_SPREAD:
            return self._build_call_credit_spread(
                chain, expiration, price, spread_width, target_delta,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.CALL_DEBIT_SPREAD:
            return self._build_call_debit_spread(
                chain, expiration, price, spread_width,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.PUT_DEBIT_SPREAD:
            return self._build_put_debit_spread(
                chain, expiration, price, spread_width,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.IRON_CONDOR:
            return self._build_iron_condor(
                chain, expiration, price, spread_width, target_delta,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.LONG_STRADDLE:
            return self._build_long_straddle(
                chain, expiration, price,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.LONG_STRANGLE:
            return self._build_long_strangle(
                chain, expiration, price,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.LONG_CALL:
            return self._build_long_option(
                chain, expiration, price, OptionType.CALL,
                signal, capital, risk_fraction,
            )
        elif strategy_type == OptionsStrategyType.LONG_PUT:
            return self._build_long_option(
                chain, expiration, price, OptionType.PUT,
                signal, capital, risk_fraction,
            )
        return None

    def _find_strike_by_delta(
        self, chain: OptionChainSnapshot, expiration: str,
        option_type: OptionType, target_delta: float,
    ) -> Optional[float]:
        """Find the strike closest to the target delta."""
        options = chain.calls if option_type == OptionType.CALL else chain.puts
        best_strike = None
        best_diff = float("inf")

        for (exp, strike), leg in options.items():
            if exp != expiration:
                continue
            diff = abs(abs(leg.delta) - target_delta)
            if diff < best_diff:
                best_diff = diff
                best_strike = strike

        return best_strike

    def _find_atm_strike(
        self, chain: OptionChainSnapshot, expiration: str,
    ) -> Optional[float]:
        """Find the strike closest to ATM."""
        strikes = chain.strikes_for_expiration(expiration)
        if not strikes:
            return None
        price = chain.underlying_price
        return min(strikes, key=lambda s: abs(s - price))

    def _size_contracts(
        self, max_loss_per_contract: float, capital: float, risk_fraction: float,
    ) -> int:
        """Calculate number of contracts based on defined risk."""
        if max_loss_per_contract <= 0:
            return 0
        risk_amount = capital * risk_fraction
        contracts = int(risk_amount / max_loss_per_contract)
        return max(1, min(contracts, settings.max_contracts_per_trade))

    def _build_put_credit_spread(
        self, chain, expiration, price, spread_width, target_delta,
        signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Sell higher put, buy lower put."""
        short_strike = self._find_strike_by_delta(chain, expiration, OptionType.PUT, target_delta)
        if short_strike is None:
            return None

        long_strike = short_strike - spread_width
        short_leg_data = chain.get_put(expiration, short_strike)
        long_leg_data = chain.get_put(expiration, long_strike)

        if not short_leg_data or not long_leg_data:
            return None

        credit = short_leg_data.premium - long_leg_data.premium
        if credit <= 0:
            return None

        max_loss_per = (spread_width - credit) * 100
        max_profit_per = credit * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        short_leg = OptionLeg(
            contract_symbol=short_leg_data.contract_symbol,
            option_type=OptionType.PUT,
            strike=short_strike,
            expiration=expiration,
            action=OptionAction.SELL_TO_OPEN,
            quantity=contracts,
            premium=short_leg_data.premium,
            delta=short_leg_data.delta,
            gamma=short_leg_data.gamma,
            theta=short_leg_data.theta,
            vega=short_leg_data.vega,
            iv=short_leg_data.iv,
        )
        long_leg = OptionLeg(
            contract_symbol=long_leg_data.contract_symbol,
            option_type=OptionType.PUT,
            strike=long_strike,
            expiration=expiration,
            action=OptionAction.BUY_TO_OPEN,
            quantity=contracts,
            premium=long_leg_data.premium,
            delta=long_leg_data.delta,
            gamma=long_leg_data.gamma,
            theta=long_leg_data.theta,
            vega=long_leg_data.vega,
            iv=long_leg_data.iv,
        )

        net_delta = (short_leg_data.delta + long_leg_data.delta) * contracts
        net_theta = (short_leg_data.theta + long_leg_data.theta) * contracts

        return OptionsOrder(
            strategy_type=OptionsStrategyType.PUT_CREDIT_SPREAD,
            legs=[short_leg, long_leg],
            underlying_price=price,
            net_premium=-credit,  # negative = credit received
            max_loss=max_loss_per * contracts / 100,
            max_profit=max_profit_per * contracts / 100,
            contracts=contracts,
            net_delta=net_delta,
            net_theta=net_theta,
            signal_strategy=signal.strategy,
            regime="",
            confidence=signal.confidence,
        )

    def _build_call_credit_spread(
        self, chain, expiration, price, spread_width, target_delta,
        signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Sell lower call, buy higher call."""
        short_strike = self._find_strike_by_delta(chain, expiration, OptionType.CALL, target_delta)
        if short_strike is None:
            return None

        long_strike = short_strike + spread_width
        short_leg_data = chain.get_call(expiration, short_strike)
        long_leg_data = chain.get_call(expiration, long_strike)

        if not short_leg_data or not long_leg_data:
            return None

        credit = short_leg_data.premium - long_leg_data.premium
        if credit <= 0:
            return None

        max_loss_per = (spread_width - credit) * 100
        max_profit_per = credit * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        short_leg = OptionLeg(
            contract_symbol=short_leg_data.contract_symbol,
            option_type=OptionType.CALL,
            strike=short_strike,
            expiration=expiration,
            action=OptionAction.SELL_TO_OPEN,
            quantity=contracts,
            premium=short_leg_data.premium,
            delta=short_leg_data.delta,
            gamma=short_leg_data.gamma,
            theta=short_leg_data.theta,
            vega=short_leg_data.vega,
            iv=short_leg_data.iv,
        )
        long_leg = OptionLeg(
            contract_symbol=long_leg_data.contract_symbol,
            option_type=OptionType.CALL,
            strike=long_strike,
            expiration=expiration,
            action=OptionAction.BUY_TO_OPEN,
            quantity=contracts,
            premium=long_leg_data.premium,
            delta=long_leg_data.delta,
            gamma=long_leg_data.gamma,
            theta=long_leg_data.theta,
            vega=long_leg_data.vega,
            iv=long_leg_data.iv,
        )

        net_delta = (short_leg_data.delta + long_leg_data.delta) * contracts
        net_theta = (short_leg_data.theta + long_leg_data.theta) * contracts

        return OptionsOrder(
            strategy_type=OptionsStrategyType.CALL_CREDIT_SPREAD,
            legs=[short_leg, long_leg],
            underlying_price=price,
            net_premium=-credit,
            max_loss=max_loss_per * contracts / 100,
            max_profit=max_profit_per * contracts / 100,
            contracts=contracts,
            net_delta=net_delta,
            net_theta=net_theta,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )

    def _build_call_debit_spread(
        self, chain, expiration, price, spread_width,
        signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Buy ATM call, sell OTM call."""
        long_strike = self._find_atm_strike(chain, expiration)
        if long_strike is None:
            return None

        short_strike = long_strike + spread_width
        long_leg_data = chain.get_call(expiration, long_strike)
        short_leg_data = chain.get_call(expiration, short_strike)

        if not long_leg_data or not short_leg_data:
            return None

        debit = long_leg_data.premium - short_leg_data.premium
        if debit <= 0:
            return None

        max_loss_per = debit * 100
        max_profit_per = (spread_width - debit) * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        long_leg = OptionLeg(
            contract_symbol=long_leg_data.contract_symbol,
            option_type=OptionType.CALL,
            strike=long_strike,
            expiration=expiration,
            action=OptionAction.BUY_TO_OPEN,
            quantity=contracts,
            premium=long_leg_data.premium,
            delta=long_leg_data.delta, gamma=long_leg_data.gamma,
            theta=long_leg_data.theta, vega=long_leg_data.vega, iv=long_leg_data.iv,
        )
        short_leg = OptionLeg(
            contract_symbol=short_leg_data.contract_symbol,
            option_type=OptionType.CALL,
            strike=short_strike,
            expiration=expiration,
            action=OptionAction.SELL_TO_OPEN,
            quantity=contracts,
            premium=short_leg_data.premium,
            delta=short_leg_data.delta, gamma=short_leg_data.gamma,
            theta=short_leg_data.theta, vega=short_leg_data.vega, iv=short_leg_data.iv,
        )

        net_delta = (long_leg_data.delta + short_leg_data.delta) * contracts
        net_theta = (long_leg_data.theta + short_leg_data.theta) * contracts

        return OptionsOrder(
            strategy_type=OptionsStrategyType.CALL_DEBIT_SPREAD,
            legs=[long_leg, short_leg],
            underlying_price=price,
            net_premium=debit,
            max_loss=max_loss_per * contracts / 100,
            max_profit=max_profit_per * contracts / 100,
            contracts=contracts,
            net_delta=net_delta,
            net_theta=net_theta,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )

    def _build_put_debit_spread(
        self, chain, expiration, price, spread_width,
        signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Buy ATM put, sell OTM put."""
        long_strike = self._find_atm_strike(chain, expiration)
        if long_strike is None:
            return None

        short_strike = long_strike - spread_width
        long_leg_data = chain.get_put(expiration, long_strike)
        short_leg_data = chain.get_put(expiration, short_strike)

        if not long_leg_data or not short_leg_data:
            return None

        debit = long_leg_data.premium - short_leg_data.premium
        if debit <= 0:
            return None

        max_loss_per = debit * 100
        max_profit_per = (spread_width - debit) * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        long_leg = OptionLeg(
            contract_symbol=long_leg_data.contract_symbol,
            option_type=OptionType.PUT,
            strike=long_strike,
            expiration=expiration,
            action=OptionAction.BUY_TO_OPEN,
            quantity=contracts,
            premium=long_leg_data.premium,
            delta=long_leg_data.delta, gamma=long_leg_data.gamma,
            theta=long_leg_data.theta, vega=long_leg_data.vega, iv=long_leg_data.iv,
        )
        short_leg = OptionLeg(
            contract_symbol=short_leg_data.contract_symbol,
            option_type=OptionType.PUT,
            strike=short_strike,
            expiration=expiration,
            action=OptionAction.SELL_TO_OPEN,
            quantity=contracts,
            premium=short_leg_data.premium,
            delta=short_leg_data.delta, gamma=short_leg_data.gamma,
            theta=short_leg_data.theta, vega=short_leg_data.vega, iv=short_leg_data.iv,
        )

        net_delta = (long_leg_data.delta + short_leg_data.delta) * contracts
        net_theta = (long_leg_data.theta + short_leg_data.theta) * contracts

        return OptionsOrder(
            strategy_type=OptionsStrategyType.PUT_DEBIT_SPREAD,
            legs=[long_leg, short_leg],
            underlying_price=price,
            net_premium=debit,
            max_loss=max_loss_per * contracts / 100,
            max_profit=max_profit_per * contracts / 100,
            contracts=contracts,
            net_delta=net_delta,
            net_theta=net_theta,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )

    def _build_iron_condor(
        self, chain, expiration, price, spread_width, target_delta,
        signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Put credit spread + call credit spread."""
        # Put side
        put_short = self._find_strike_by_delta(chain, expiration, OptionType.PUT, target_delta)
        if put_short is None:
            return None
        put_long = put_short - spread_width

        # Call side
        call_short = self._find_strike_by_delta(chain, expiration, OptionType.CALL, target_delta)
        if call_short is None:
            return None
        call_long = call_short + spread_width

        ps = chain.get_put(expiration, put_short)
        pl = chain.get_put(expiration, put_long)
        cs = chain.get_call(expiration, call_short)
        cl = chain.get_call(expiration, call_long)

        if not all([ps, pl, cs, cl]):
            return None

        put_credit = ps.premium - pl.premium
        call_credit = cs.premium - cl.premium
        total_credit = put_credit + call_credit

        if total_credit <= 0:
            return None

        max_loss_per = (spread_width - total_credit) * 100
        max_profit_per = total_credit * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        legs = [
            OptionLeg(ps.contract_symbol, OptionType.PUT, put_short, expiration,
                      OptionAction.SELL_TO_OPEN, contracts, ps.premium,
                      ps.delta, ps.gamma, ps.theta, ps.vega, ps.iv),
            OptionLeg(pl.contract_symbol, OptionType.PUT, put_long, expiration,
                      OptionAction.BUY_TO_OPEN, contracts, pl.premium,
                      pl.delta, pl.gamma, pl.theta, pl.vega, pl.iv),
            OptionLeg(cs.contract_symbol, OptionType.CALL, call_short, expiration,
                      OptionAction.SELL_TO_OPEN, contracts, cs.premium,
                      cs.delta, cs.gamma, cs.theta, cs.vega, cs.iv),
            OptionLeg(cl.contract_symbol, OptionType.CALL, call_long, expiration,
                      OptionAction.BUY_TO_OPEN, contracts, cl.premium,
                      cl.delta, cl.gamma, cl.theta, cl.vega, cl.iv),
        ]

        net_delta = sum(l.delta for l in [ps, pl, cs, cl]) * contracts
        net_theta = sum(l.theta for l in [ps, pl, cs, cl]) * contracts

        return OptionsOrder(
            strategy_type=OptionsStrategyType.IRON_CONDOR,
            legs=legs,
            underlying_price=price,
            net_premium=-total_credit,
            max_loss=max_loss_per * contracts / 100,
            max_profit=max_profit_per * contracts / 100,
            contracts=contracts,
            net_delta=net_delta,
            net_theta=net_theta,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )

    def _build_long_straddle(
        self, chain, expiration, price, signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Buy ATM call + ATM put."""
        atm = self._find_atm_strike(chain, expiration)
        if atm is None:
            return None

        call = chain.get_call(expiration, atm)
        put = chain.get_put(expiration, atm)
        if not call or not put:
            return None

        total_debit = call.premium + put.premium
        max_loss_per = total_debit * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        legs = [
            OptionLeg(call.contract_symbol, OptionType.CALL, atm, expiration,
                      OptionAction.BUY_TO_OPEN, contracts, call.premium,
                      call.delta, call.gamma, call.theta, call.vega, call.iv),
            OptionLeg(put.contract_symbol, OptionType.PUT, atm, expiration,
                      OptionAction.BUY_TO_OPEN, contracts, put.premium,
                      put.delta, put.gamma, put.theta, put.vega, put.iv),
        ]

        return OptionsOrder(
            strategy_type=OptionsStrategyType.LONG_STRADDLE,
            legs=legs,
            underlying_price=price,
            net_premium=total_debit,
            max_loss=max_loss_per * contracts / 100,
            max_profit=999999.0,  # theoretically unlimited
            contracts=contracts,
            net_delta=(call.delta + put.delta) * contracts,
            net_theta=(call.theta + put.theta) * contracts,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )

    def _build_long_strangle(
        self, chain, expiration, price, signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Buy OTM call + OTM put (delta ~0.25-0.30)."""
        call_strike = self._find_strike_by_delta(chain, expiration, OptionType.CALL, 0.28)
        put_strike = self._find_strike_by_delta(chain, expiration, OptionType.PUT, 0.28)

        if call_strike is None or put_strike is None:
            return None

        call = chain.get_call(expiration, call_strike)
        put = chain.get_put(expiration, put_strike)
        if not call or not put:
            return None

        total_debit = call.premium + put.premium
        max_loss_per = total_debit * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        legs = [
            OptionLeg(call.contract_symbol, OptionType.CALL, call_strike, expiration,
                      OptionAction.BUY_TO_OPEN, contracts, call.premium,
                      call.delta, call.gamma, call.theta, call.vega, call.iv),
            OptionLeg(put.contract_symbol, OptionType.PUT, put_strike, expiration,
                      OptionAction.BUY_TO_OPEN, contracts, put.premium,
                      put.delta, put.gamma, put.theta, put.vega, put.iv),
        ]

        return OptionsOrder(
            strategy_type=OptionsStrategyType.LONG_STRANGLE,
            legs=legs,
            underlying_price=price,
            net_premium=total_debit,
            max_loss=max_loss_per * contracts / 100,
            max_profit=999999.0,
            contracts=contracts,
            net_delta=(call.delta + put.delta) * contracts,
            net_theta=(call.theta + put.theta) * contracts,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )

    def _build_long_option(
        self, chain, expiration, price, option_type: OptionType,
        signal, capital, risk_fraction,
    ) -> Optional[OptionsOrder]:
        """Buy a single ATM option."""
        atm = self._find_atm_strike(chain, expiration)
        if atm is None:
            return None

        if option_type == OptionType.CALL:
            leg_data = chain.get_call(expiration, atm)
            strat_type = OptionsStrategyType.LONG_CALL
        else:
            leg_data = chain.get_put(expiration, atm)
            strat_type = OptionsStrategyType.LONG_PUT

        if not leg_data:
            return None

        max_loss_per = leg_data.premium * 100
        contracts = self._size_contracts(max_loss_per, capital, risk_fraction)

        leg = OptionLeg(
            contract_symbol=leg_data.contract_symbol,
            option_type=option_type,
            strike=atm,
            expiration=expiration,
            action=OptionAction.BUY_TO_OPEN,
            quantity=contracts,
            premium=leg_data.premium,
            delta=leg_data.delta, gamma=leg_data.gamma,
            theta=leg_data.theta, vega=leg_data.vega, iv=leg_data.iv,
        )

        return OptionsOrder(
            strategy_type=strat_type,
            legs=[leg],
            underlying_price=price,
            net_premium=leg_data.premium,
            max_loss=max_loss_per * contracts / 100,
            max_profit=999999.0,
            contracts=contracts,
            net_delta=leg_data.delta * contracts,
            net_theta=leg_data.theta * contracts,
            signal_strategy=signal.strategy,
            confidence=signal.confidence,
        )
