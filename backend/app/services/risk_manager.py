"""Risk management: position sizing, daily limits, circuit breakers, adaptive Kelly."""

from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import logging
import math

from app.config import settings
from app.services.strategies.base import TradeSignal

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class RiskManager:
    """Enforces all risk management rules with adaptive Kelly sizing."""

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

        # Adaptive Kelly tracking (rolling window of last 50 trades)
        self._trade_results: list[float] = []
        self._kelly_window = 50
        self._kelly_fraction = 0.25  # Use quarter-Kelly for safety

    def _kelly_risk_fraction(self) -> float:
        """Compute Kelly Criterion-based risk fraction from recent trade history.

        Full Kelly = W - (1-W)/R where W=win_rate, R=avg_win/avg_loss.
        We use fractional Kelly (quarter-Kelly) for safety.
        Falls back to the configured max_risk_per_trade when insufficient data.
        """
        if len(self._trade_results) < 10:
            return self.max_risk_per_trade

        wins = [r for r in self._trade_results if r > 0]
        losses = [r for r in self._trade_results if r <= 0]

        if not wins or not losses:
            return self.max_risk_per_trade

        win_rate = len(wins) / len(self._trade_results)
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))

        if avg_loss == 0:
            return self.max_risk_per_trade

        payoff_ratio = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / payoff_ratio

        # Clamp: never risk more than configured max, never go negative
        kelly_adjusted = max(0.002, min(kelly * self._kelly_fraction, self.max_risk_per_trade))
        return kelly_adjusted

    def _time_of_day_scalar(self) -> float:
        """Scale position size based on time of day."""
        from datetime import time as dt_time
        now = datetime.now(ET).time()
        if now < dt_time(9, 45):
            return 0.5  # Opening volatility
        elif now < dt_time(11, 30):
            return 1.0  # Best setups
        elif now < dt_time(13, 30):
            return 0.6  # Lunch chop
        elif now < dt_time(15, 30):
            return 0.8  # Afternoon
        else:
            return 0.5  # EOD volatility

    def calculate_position_size(
        self, signal: TradeSignal, capital: float
    ) -> int:
        """Calculate position size using adaptive Kelly Criterion."""
        risk_fraction = self._kelly_risk_fraction()
        # Scale risk by signal confidence (min 30% of normal size)
        risk_fraction *= max(0.3, min(signal.confidence, 1.0))
        risk_fraction *= self._time_of_day_scalar()
        risk_amount = capital * risk_fraction
        stop_distance = abs(signal.entry_price - signal.stop_loss)
        if stop_distance <= 0:
            return 0

        quantity = int(risk_amount / stop_distance)

        # Position cap
        max_shares = int((capital * self.max_position_pct) / signal.entry_price)
        quantity = min(quantity, max_shares)

        # Reduce size during losing streaks (scale down by 25% per consecutive loss)
        if self.consecutive_losses > 0:
            scale = max(0.25, 1.0 - 0.25 * self.consecutive_losses)
            quantity = max(1, int(quantity * scale))

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
        if self.cooldown_until and datetime.now(ET) < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now(ET)).seconds // 60
            return False, f"Cooling off: {remaining} min remaining after {self.cooldown_after_losses} consecutive losses"

        return True, "OK"

    def record_trade_result(self, pnl: float):
        """Update consecutive loss counter, trigger cooldown, and track for Kelly sizing."""
        # Track for adaptive Kelly
        self._trade_results.append(pnl)
        if len(self._trade_results) > self._kelly_window:
            self._trade_results = self._trade_results[-self._kelly_window:]

        if pnl <= 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.cooldown_after_losses:
                self.cooldown_until = datetime.now(ET) + timedelta(minutes=self.cooldown_minutes)
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
        self.circuit_breaker_active = False

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
            "cooldown_active": bool(self.cooldown_until and datetime.now(ET) < self.cooldown_until),
            "circuit_breaker_active": self.circuit_breaker_active,
        }
