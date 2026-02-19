"""Options domain models: legs, orders, chain snapshots."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class OptionsStrategyType(str, Enum):
    LONG_CALL = "LONG_CALL"
    LONG_PUT = "LONG_PUT"
    CALL_DEBIT_SPREAD = "CALL_DEBIT_SPREAD"
    CALL_CREDIT_SPREAD = "CALL_CREDIT_SPREAD"
    PUT_DEBIT_SPREAD = "PUT_DEBIT_SPREAD"
    PUT_CREDIT_SPREAD = "PUT_CREDIT_SPREAD"
    IRON_CONDOR = "IRON_CONDOR"
    LONG_STRADDLE = "LONG_STRADDLE"
    LONG_STRANGLE = "LONG_STRANGLE"


class OptionAction(str, Enum):
    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"


# Abbreviations for display
STRATEGY_ABBREV = {
    OptionsStrategyType.LONG_CALL: "LC",
    OptionsStrategyType.LONG_PUT: "LP",
    OptionsStrategyType.CALL_DEBIT_SPREAD: "CDS",
    OptionsStrategyType.CALL_CREDIT_SPREAD: "CCS",
    OptionsStrategyType.PUT_DEBIT_SPREAD: "PDS",
    OptionsStrategyType.PUT_CREDIT_SPREAD: "PCS",
    OptionsStrategyType.IRON_CONDOR: "IC",
    OptionsStrategyType.LONG_STRADDLE: "STR",
    OptionsStrategyType.LONG_STRANGLE: "STRG",
}

# Exit rules for each options strategy type
# take_profit_pct: fraction of max profit to take profit at
# initial_stop_mult: initial stop loss multiplier (wider to give room)
# tight_stop_mult: tighter stop as position ages
# dte_tighten: DTE at which stop transitions from initial to tight
# target_win_rate: expected win rate for Kelly sizing reference
OPTIONS_EXIT_RULES: dict[OptionsStrategyType, dict] = {
    OptionsStrategyType.PUT_CREDIT_SPREAD: {
        "take_profit_pct": 0.50, "initial_stop_mult": 3.0, "tight_stop_mult": 1.5,
        "dte_tighten": 3, "target_win_rate": 0.78,
    },
    OptionsStrategyType.CALL_CREDIT_SPREAD: {
        "take_profit_pct": 0.50, "initial_stop_mult": 3.0, "tight_stop_mult": 1.5,
        "dte_tighten": 3, "target_win_rate": 0.78,
    },
    OptionsStrategyType.PUT_DEBIT_SPREAD: {
        "take_profit_pct": 1.00, "initial_stop_mult": 0.65, "tight_stop_mult": 0.40,
        "dte_tighten": 3, "target_win_rate": 0.50,
    },
    OptionsStrategyType.CALL_DEBIT_SPREAD: {
        "take_profit_pct": 1.00, "initial_stop_mult": 0.65, "tight_stop_mult": 0.40,
        "dte_tighten": 3, "target_win_rate": 0.50,
    },
    OptionsStrategyType.IRON_CONDOR: {
        "take_profit_pct": 0.50, "initial_stop_mult": 2.5, "tight_stop_mult": 1.5,
        "dte_tighten": 3, "target_win_rate": 0.70,
    },
    OptionsStrategyType.LONG_CALL: {
        "take_profit_pct": 0.80, "initial_stop_mult": 0.50, "tight_stop_mult": 0.30,
        "dte_tighten": 3, "target_win_rate": 0.42,
    },
    OptionsStrategyType.LONG_PUT: {
        "take_profit_pct": 0.80, "initial_stop_mult": 0.50, "tight_stop_mult": 0.30,
        "dte_tighten": 3, "target_win_rate": 0.42,
    },
    OptionsStrategyType.LONG_STRADDLE: {
        "take_profit_pct": 0.80, "initial_stop_mult": 0.40, "tight_stop_mult": 0.25,
        "dte_tighten": 2, "target_win_rate": 0.38,
    },
    OptionsStrategyType.LONG_STRANGLE: {
        "take_profit_pct": 1.00, "initial_stop_mult": 0.45, "tight_stop_mult": 0.30,
        "dte_tighten": 2, "target_win_rate": 0.35,
    },
}


@dataclass
class OptionLeg:
    """A single option contract leg."""
    contract_symbol: str          # OCC symbol e.g. SPY250228C00590000
    option_type: OptionType
    strike: float
    expiration: str               # YYYY-MM-DD
    action: OptionAction
    quantity: int
    premium: float                # per-contract premium
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0

    def to_dict(self) -> dict:
        return {
            "contract_symbol": self.contract_symbol,
            "option_type": self.option_type.value,
            "strike": self.strike,
            "expiration": self.expiration,
            "action": self.action.value,
            "quantity": self.quantity,
            "premium": self.premium,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "iv": self.iv,
        }


@dataclass
class OptionsOrder:
    """A complete options order with one or more legs."""
    strategy_type: OptionsStrategyType
    legs: list[OptionLeg]
    underlying_price: float
    net_premium: float            # positive = debit, negative = credit
    max_loss: float               # always positive
    max_profit: float             # always positive
    contracts: int
    net_delta: float = 0.0
    net_theta: float = 0.0
    signal_strategy: str = ""     # which signal strategy triggered this
    regime: str = ""
    confidence: float = 0.0

    @property
    def is_credit(self) -> bool:
        return self.net_premium < 0

    @property
    def primary_strike(self) -> float:
        """Return the main strike for display."""
        if self.legs:
            return self.legs[0].strike
        return 0.0

    @property
    def primary_expiration(self) -> str:
        if self.legs:
            return self.legs[0].expiration
        return ""

    @property
    def primary_option_type(self) -> str:
        if self.legs:
            return self.legs[0].option_type.value
        return ""

    def legs_to_json(self) -> list[dict]:
        return [leg.to_dict() for leg in self.legs]

    def to_display_string(self) -> str:
        """Human-readable description of the order."""
        abbrev = STRATEGY_ABBREV.get(self.strategy_type, self.strategy_type.value)
        strikes = "/".join(f"${leg.strike:.0f}{leg.option_type.value[0]}" for leg in self.legs)
        exp = self.primary_expiration
        if self.is_credit:
            return f"{abbrev}: {strikes}, {exp}, ${abs(self.net_premium):.2f} credit, {self.contracts} ct"
        else:
            return f"{abbrev}: {strikes}, {exp}, ${self.net_premium:.2f} debit, {self.contracts} ct"


@dataclass
class OptionChainSnapshot:
    """Snapshot of the options chain at a point in time."""
    underlying_price: float
    timestamp: datetime
    expirations: list[str]        # available expiration dates
    calls: dict = field(default_factory=dict)   # (exp, strike) -> OptionLeg
    puts: dict = field(default_factory=dict)    # (exp, strike) -> OptionLeg
    iv_rank: float = 50.0         # 0-100 percentile
    iv_percentile: float = 50.0   # 0-100 percentile

    def get_call(self, expiration: str, strike: float) -> Optional[OptionLeg]:
        return self.calls.get((expiration, strike))

    def get_put(self, expiration: str, strike: float) -> Optional[OptionLeg]:
        return self.puts.get((expiration, strike))

    def strikes_for_expiration(self, expiration: str) -> list[float]:
        """Get sorted list of available strikes for an expiration."""
        strikes = set()
        for (exp, strike) in self.calls:
            if exp == expiration:
                strikes.add(strike)
        for (exp, strike) in self.puts:
            if exp == expiration:
                strikes.add(strike)
        return sorted(strikes)
