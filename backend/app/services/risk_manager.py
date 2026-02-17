"""Risk management: position sizing, daily limits, circuit breakers."""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
import logging

from app.config import settings
from app.services.strategies.base import TradeSignal

logger = logging.getLogger(__name__)


class RiskManager:
    """Enforces all risk management rules."""

    def __init__(self):
        self.max_risk_per_trade = settings.max_risk_per_trade
        self.daily_loss_limit = settings.daily_loss_limit
        self.max_drawdown = settings.max_drawdown
        self.max_position_pct = settings.max_position_pct
        self.max_trades_per_day = settings.max_trades_per_day
        self.cooldown_after_losses = settings.cooldown_after_consecutive_losses
        self.cooldown_minutes = settings.cooldown_minutes

        self.consecutive_losses = 0
        self.cooldown_until: Optional[datetime] = None
        self.circuit_breaker_active = False

    def calculate_position_size(
        self, signal: TradeSignal, capital: float
    ) -> int:
        """Calculate position size based on risk per trade and stop distance."""
        risk_amount = capital * self.max_risk_per_trade
        stop_distance = abs(signal.entry_price - signal.stop_loss)
        if stop_distance <= 0:
            return 0

        quantity = int(risk_amount / stop_distance)

        # Position cap
        max_shares = int((capital * self.max_position_pct) / signal.entry_price)
        quantity = min(quantity, max_shares)

        return max(quantity, 0)

    def can_trade(
        self,
        capital: float,
        peak_capital: float,
        daily_pnl: float,
        trades_today: int,
    ) -> tuple[bool, str]:
        """Check if trading is allowed given current risk state."""

        # Circuit breaker: max drawdown
        if peak_capital > 0:
            drawdown = (peak_capital - capital) / peak_capital
            if drawdown >= self.max_drawdown:
                self.circuit_breaker_active = True
                return False, f"Circuit breaker: drawdown {drawdown:.1%} >= {self.max_drawdown:.1%}"

        # Daily loss limit
        daily_limit = capital * self.daily_loss_limit
        if daily_pnl <= -daily_limit:
            return False, f"Daily loss limit hit: ${daily_pnl:.2f} <= -${daily_limit:.2f}"

        # Max trades per day
        if trades_today >= self.max_trades_per_day:
            return False, f"Max trades per day reached: {trades_today}/{self.max_trades_per_day}"

        # Cooldown after consecutive losses
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).seconds // 60
            return False, f"Cooling off: {remaining} min remaining after {self.cooldown_after_losses} consecutive losses"

        return True, "OK"

    def record_trade_result(self, pnl: float):
        """Update consecutive loss counter and trigger cooldown if needed."""
        if pnl <= 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.cooldown_after_losses:
                self.cooldown_until = datetime.now() + timedelta(minutes=self.cooldown_minutes)
                logger.warning(
                    f"Cooldown triggered: {self.consecutive_losses} consecutive losses. "
                    f"Resuming at {self.cooldown_until}"
                )
        else:
            self.consecutive_losses = 0
            self.cooldown_until = None

    def reset_daily(self):
        """Reset daily counters (call at start of each trading day)."""
        self.consecutive_losses = 0
        self.cooldown_until = None

    def get_metrics(
        self, capital: float, peak_capital: float, daily_pnl: float, trades_today: int
    ) -> dict:
        drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
        return {
            "current_drawdown_pct": round(drawdown * 100, 2),
            "max_drawdown_limit": self.max_drawdown * 100,
            "daily_loss": round(daily_pnl, 2),
            "daily_loss_limit": round(capital * self.daily_loss_limit, 2),
            "trades_today": trades_today,
            "max_trades_per_day": self.max_trades_per_day,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_active": bool(self.cooldown_until and datetime.now() < self.cooldown_until),
            "circuit_breaker_active": self.circuit_breaker_active,
        }
