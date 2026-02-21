"""Paper options trading engine — simulates options P&L via Greeks approximation."""

from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.options.models import (
    OptionsOrder, OptionsStrategyType, OPTIONS_EXIT_RULES, STRATEGY_ABBREV,
)
from app.services.options import pricing

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class PaperOptionPosition:
    """Tracks an open options position with Greeks-based P&L estimation."""

    def __init__(self, order: OptionsOrder, entry_time: datetime):
        self.order = order
        self.entry_time = entry_time
        self.entry_underlying = order.underlying_price
        self.entry_net_premium = abs(order.net_premium)
        self.current_premium = abs(order.net_premium)

        # Track extremes for MAE/MFE
        self.best_premium = self.current_premium
        self.worst_premium = self.current_premium
        self.highest_underlying = order.underlying_price
        self.lowest_underlying = order.underlying_price

        # Commission
        total_legs = sum(leg.quantity for leg in order.legs)
        self.commission = total_legs * settings.options_commission_per_contract * 2  # open + close

        # Spread cost simulation (5-10% of premium)
        self.spread_cost = self.entry_net_premium * 0.07

        # Collateral held by broker (set by engine on open)
        self.collateral = 0.0

    @property
    def strategy_type(self) -> OptionsStrategyType:
        return self.order.strategy_type

    @property
    def is_credit(self) -> bool:
        return self.order.is_credit

    def update(self, underlying_price: float, dt_days: float = 0.00396):
        """Update position value using Greeks approximation.

        dt_days: time elapsed in trading days (~1/252 per bar for 1-min bars).
        """
        dS = underlying_price - self.entry_underlying

        # Aggregate Greeks from all legs (net position)
        net_delta = 0.0
        net_gamma = 0.0
        net_theta = 0.0

        for leg in self.order.legs:
            sign = -1.0 if "SELL" in leg.action.value else 1.0
            net_delta += sign * leg.delta * leg.quantity
            net_gamma += sign * leg.gamma * leg.quantity
            net_theta += sign * leg.theta * leg.quantity

        # Premium change estimate
        premium_change = pricing.estimate_premium_change(
            net_delta, net_gamma, net_theta, dS, dt_days,
        )

        # For credit spreads, we want the premium to decrease (we sold it)
        if self.is_credit:
            self.current_premium = max(0, self.entry_net_premium + premium_change)
        else:
            self.current_premium = max(0, self.entry_net_premium + premium_change)

        # Track extremes
        self.highest_underlying = max(self.highest_underlying, underlying_price)
        self.lowest_underlying = min(self.lowest_underlying, underlying_price)
        self.best_premium = max(self.best_premium, self.current_premium) if not self.is_credit else min(self.best_premium, self.current_premium)
        self.worst_premium = min(self.worst_premium, self.current_premium) if not self.is_credit else max(self.worst_premium, self.current_premium)

    def raw_pnl(self) -> float:
        """Raw P&L from premium movement only (no commission/spread cost).
        Used for stop-loss and take-profit checks."""
        if self.is_credit:
            return (self.entry_net_premium - self.current_premium) * self.order.contracts * 100
        else:
            return (self.current_premium - self.entry_net_premium) * self.order.contracts * 100

    def unrealized_pnl(self) -> float:
        """Net P&L including commission and spread cost."""
        return self.raw_pnl() - self.commission - (self.spread_cost * self.order.contracts * 100)

    def pnl_pct_of_max(self) -> float:
        """P&L as percentage of max profit (for exit rules)."""
        if self.order.max_profit <= 0:
            return 0.0
        return self.unrealized_pnl() / (self.order.max_profit * self.order.contracts)

    def loss_pct(self) -> float:
        """Loss as percentage of entry premium or max loss."""
        pnl = self.unrealized_pnl()
        if pnl >= 0:
            return 0.0
        if self.is_credit:
            return abs(pnl) / (self.entry_net_premium * self.order.contracts * 100)
        else:
            return abs(pnl) / (self.entry_net_premium * self.order.contracts * 100)


class PaperOptionsEngine:
    """Paper trading engine for options — manages portfolio of defined-risk positions."""

    def __init__(self, initial_capital: float = 25000.0):
        self.capital = initial_capital          # Free cash (collateral deducted while position open)
        self.initial_capital = initial_capital
        self.peak_capital = initial_capital     # DEPRECATED: kept for backward compat
        self.position: Optional[PaperOptionPosition] = None
        self.closed_trades: list[dict] = []
        # Peak total-equity tracking (mark-to-market, updated every total_equity() call)
        self._peak_equity: float = initial_capital
        self._last_equity: float = initial_capital

    def open_position(self, order: OptionsOrder) -> Optional[PaperOptionPosition]:
        """Open a new options position."""
        if self.position is not None:
            logger.warning("Already have an open options position")
            return None

        now = datetime.now(ET)
        pos = PaperOptionPosition(order, now)

        if order.is_credit:
            # Credit spreads: broker requires collateral = max_loss per contract
            # max_loss = (spread_width - credit) * contracts * 100
            collateral = order.max_loss
            if collateral > self.capital:
                logger.warning(
                    f"Insufficient capital for credit spread collateral: "
                    f"need ${collateral:.0f}, have ${self.capital:.0f}"
                )
                return None
            self.capital -= collateral
            pos.collateral = collateral
        else:
            # Debit positions: pay the premium
            cost = abs(order.net_premium) * order.contracts * 100
            cost += pos.spread_cost * order.contracts * 100
            if cost > self.capital:
                logger.warning(
                    f"Insufficient capital for debit position: "
                    f"need ${cost:.0f}, have ${self.capital:.0f}"
                )
                return None
            self.capital -= cost
            pos.collateral = cost

        self.position = pos
        display = order.to_display_string()
        logger.info(f"Paper OPTIONS OPEN: {display} | Collateral: ${pos.collateral:.0f}")
        return self.position

    def close_position(
        self, underlying_price: float, reason: str = "manual",
    ) -> Optional[dict]:
        """Close the current options position."""
        if self.position is None:
            return None

        pos = self.position
        pos.update(underlying_price)

        pnl = pos.unrealized_pnl()

        # Return collateral + P&L
        # On open, we deducted collateral. Now return it plus/minus P&L.
        self.capital += pos.collateral + pnl

        # Update both legacy peak_capital (free cash) and peak_equity (total)
        self.peak_capital = max(self.peak_capital, self.capital)
        self._last_equity = self.capital          # no open position
        self._peak_equity = max(self._peak_equity, self.capital)

        trade = self._build_trade_dict(pos, underlying_price, pnl, reason)
        self.closed_trades.append(trade)

        abbrev = STRATEGY_ABBREV.get(pos.strategy_type, pos.strategy_type.value)
        logger.info(
            f"Paper OPTIONS CLOSE {abbrev} | P&L: ${pnl:.2f} | Reason: {reason} | "
            f"Underlying: ${underlying_price:.2f}"
        )
        self.position = None
        return trade

    def _build_trade_dict(
        self, pos: PaperOptionPosition, underlying_price: float,
        pnl: float, reason: str,
    ) -> dict:
        order = pos.order
        entry_cost = pos.entry_net_premium * order.contracts * 100

        return {
            "symbol": "SPY",
            "direction": "LONG" if not order.is_credit else "SHORT",
            "strategy": order.signal_strategy,
            "quantity": order.contracts,
            "entry_price": pos.entry_net_premium,
            "exit_price": pos.current_premium,
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": datetime.now(ET).isoformat(),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / entry_cost * 100, 2) if entry_cost > 0 else 0.0,
            "exit_reason": reason,
            "confidence": order.confidence,
            "slippage": round(pos.spread_cost, 4),
            "commission": round(pos.commission, 2),
            "mae": 0.0,
            "mfe": 0.0,
            "mae_pct": 0.0,
            "mfe_pct": 0.0,
            "bars_held": None,
            # Options-specific fields
            "option_strategy_type": order.strategy_type.value,
            "contract_symbol": order.legs[0].contract_symbol if order.legs else "",
            "legs_json": json.dumps(order.legs_to_json()),
            "strike": order.primary_strike,
            "expiration_date": order.primary_expiration,
            "option_type": order.primary_option_type,
            "net_premium": round(order.net_premium, 4),
            "max_loss": round(order.max_loss, 2),
            "max_profit": round(order.max_profit, 2),
            "entry_delta": round(order.net_delta, 4),
            "entry_theta": round(order.net_theta, 4),
            "entry_iv": round(order.legs[0].iv, 4) if order.legs else 0.0,
            "underlying_entry": round(pos.entry_underlying, 2),
            "underlying_exit": round(underlying_price, 2),
            "contracts": order.contracts,
        }

    @property
    def equity(self) -> float:
        """Free cash (collateral locked while position open)."""
        return self.capital

    @property
    def buying_power(self) -> float:
        """Capital available to open new positions (= free cash)."""
        return self.capital

    @property
    def locked_collateral(self) -> float:
        """Capital currently locked as collateral / debit cost."""
        if self.position is None:
            return 0.0
        return self.position.collateral

    @property
    def peak_equity(self) -> float:
        """All-time high of total mark-to-market equity."""
        return self._peak_equity

    def total_equity(self, current_price: float) -> float:
        """
        True mark-to-market equity = free_cash + locked_collateral + unrealized_pnl.

        Also keeps peak_equity up-to-date so drawdown reflects open-position losses.
        """
        if self.position is None:
            eq = self.capital
        else:
            self.position.update(current_price)
            eq = self.capital + self.position.collateral + self.position.unrealized_pnl()

        # Update rolling peak and cache for drawdown_pct property
        self._peak_equity = max(self._peak_equity, eq)
        self._last_equity = eq
        return eq

    @property
    def drawdown_pct(self) -> float:
        """Drawdown as fraction of peak equity (mark-to-market)."""
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, (self._peak_equity - self._last_equity) / self._peak_equity)

    @property
    def daily_pnl(self) -> float:
        today = datetime.now(ET).date()
        total = 0.0
        for t in reversed(self.closed_trades):
            try:
                trade_date = datetime.fromisoformat(t["exit_time"]).date()
            except (ValueError, KeyError):
                continue
            if trade_date == today:
                total += t["pnl"]
            elif trade_date < today:
                break
        return total

    @property
    def trades_today(self) -> int:
        today = datetime.now(ET).date()
        count = 0
        for t in reversed(self.closed_trades):
            try:
                trade_date = datetime.fromisoformat(t["exit_time"]).date()
            except (ValueError, KeyError):
                continue
            if trade_date == today:
                count += 1
            elif trade_date < today:
                break
        return count

    @property
    def open_risk(self) -> float:
        """Total risk of currently open position."""
        if self.position is None:
            return 0.0
        return self.position.order.max_loss
