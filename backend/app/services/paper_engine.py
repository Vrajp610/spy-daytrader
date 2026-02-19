"""Paper trading engine - simulated order execution."""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional
import logging

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class PaperPosition:
    def __init__(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        entry_price: float,
        entry_time: datetime,
        stop_loss: float,
        take_profit: float,
        strategy: str,
    ):
        self.symbol = symbol
        self.direction = direction
        self.quantity = quantity
        self.original_quantity = quantity
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.strategy = strategy
        self.highest_since_entry = entry_price
        self.lowest_since_entry = entry_price
        self.scales_completed: list[int] = []
        self.effective_stop = stop_loss
        self.trailing_atr_mult: Optional[float] = None
        self.mae: float = 0.0  # Max adverse excursion in $
        self.mfe: float = 0.0  # Max favorable excursion in $
        self.entry_bar_count: int = 0  # Set by trading engine

    def update_extremes(self, high: float, low: float):
        self.highest_since_entry = max(self.highest_since_entry, high)
        self.lowest_since_entry = min(self.lowest_since_entry, low)
        # MAE/MFE tracking
        if self.direction == "LONG":
            self.mfe = max(self.mfe, (high - self.entry_price) * self.quantity)
            self.mae = max(self.mae, (self.entry_price - low) * self.quantity)
        else:
            self.mfe = max(self.mfe, (self.entry_price - low) * self.quantity)
            self.mae = max(self.mae, (high - self.entry_price) * self.quantity)

    def unrealized_pnl(self, current_price: float) -> float:
        if self.direction == "LONG":
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity


class PaperEngine:
    """Simulated order execution with realistic slippage."""

    def __init__(self, initial_capital: float = 25000.0):
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.peak_capital = initial_capital
        self.position: Optional[PaperPosition] = None
        self.closed_trades: list[dict] = []
        self.slippage_bps: float = 1.0  # 0.01% slippage per side

    def _apply_slippage(self, price: float, is_buy: bool, quantity: int = 0, bar_volume: int = 0) -> float:
        """Volume-dependent slippage model.
        Base: 0.5 bps for SPY (very liquid)
        Impact: scales with order size relative to bar volume.
        """
        base_bps = 0.5
        impact_bps = 0.0
        if bar_volume > 0 and quantity > 0:
            participation_rate = quantity / bar_volume
            impact_bps = participation_rate * 100  # 1% participation = 1bp extra
        total_bps = base_bps + impact_bps
        slip = price * (total_bps / 10000)
        return price + slip if is_buy else price - slip

    def open_position(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        price: float,
        stop_loss: float,
        take_profit: float,
        strategy: str,
        bar_volume: int = 0,
        confidence: float = 0.5,
    ) -> Optional[PaperPosition]:
        if self.position is not None:
            logger.warning("Already have an open position")
            return None

        is_buy = direction == "LONG"
        fill_price = self._apply_slippage(price, is_buy, quantity, bar_volume)

        self.position = PaperPosition(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=fill_price,
            entry_time=datetime.now(ET),
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
        )
        self.position.confidence = confidence
        self.position.slippage = abs(fill_price - price)

        logger.info(
            f"Paper {direction} {quantity} {symbol} @ {fill_price:.2f} "
            f"(slippage from {price:.2f})"
        )
        return self.position

    def close_position(
        self, price: float, reason: str = "manual",
        bar_volume: int = 0, current_bar_count: int = 0,
    ) -> Optional[dict]:
        if self.position is None:
            return None

        is_sell = self.position.direction == "LONG"
        fill_price = self._apply_slippage(price, not is_sell, self.position.quantity, bar_volume)

        pnl = self.position.unrealized_pnl(fill_price)
        self.capital += pnl
        self.peak_capital = max(self.peak_capital, self.capital)

        trade = {
            "symbol": self.position.symbol,
            "direction": self.position.direction,
            "quantity": self.position.quantity,
            "entry_price": self.position.entry_price,
            "exit_price": fill_price,
            "entry_time": self.position.entry_time.isoformat(),
            "exit_time": datetime.now(ET).isoformat(),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (self.position.entry_price * self.position.quantity) * 100, 2),
            "exit_reason": reason,
            "strategy": self.position.strategy,
            "confidence": getattr(self.position, 'confidence', None),
            "slippage": round(getattr(self.position, 'slippage', 0) + abs(fill_price - price), 4),
            "mae": round(self.position.mae, 2),
            "mfe": round(self.position.mfe, 2),
            "mae_pct": round(self.position.mae / (self.position.entry_price * self.position.original_quantity) * 100, 2) if self.position.original_quantity > 0 else 0.0,
            "mfe_pct": round(self.position.mfe / (self.position.entry_price * self.position.original_quantity) * 100, 2) if self.position.original_quantity > 0 else 0.0,
            "bars_held": max(0, current_bar_count - self.position.entry_bar_count) if current_bar_count > 0 else None,
        }
        self.closed_trades.append(trade)

        logger.info(
            f"Paper CLOSE {self.position.direction} {self.position.quantity} "
            f"{self.position.symbol} @ {fill_price:.2f} | P&L: ${pnl:.2f} | "
            f"Reason: {reason}"
        )
        self.position = None
        return trade

    def reduce_position(
        self, quantity: int, price: float, reason: str = "scale_out",
        bar_volume: int = 0, current_bar_count: int = 0,
    ) -> Optional[dict]:
        """Close a partial quantity of the current position."""
        if self.position is None:
            return None
        if quantity <= 0 or quantity >= self.position.quantity:
            return None

        is_sell = self.position.direction == "LONG"
        fill_price = self._apply_slippage(price, not is_sell, self.position.quantity, bar_volume)

        # P&L for just the partial quantity
        if self.position.direction == "LONG":
            pnl = (fill_price - self.position.entry_price) * quantity
        else:
            pnl = (self.position.entry_price - fill_price) * quantity

        self.capital += pnl
        self.peak_capital = max(self.peak_capital, self.capital)

        trade = {
            "symbol": self.position.symbol,
            "direction": self.position.direction,
            "quantity": quantity,
            "entry_price": self.position.entry_price,
            "exit_price": fill_price,
            "entry_time": self.position.entry_time.isoformat(),
            "exit_time": datetime.now(ET).isoformat(),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (self.position.entry_price * quantity) * 100, 2),
            "exit_reason": reason,
            "strategy": self.position.strategy,
            "is_partial": True,
            "confidence": getattr(self.position, 'confidence', None),
            "slippage": round(getattr(self.position, 'slippage', 0) + abs(fill_price - price), 4),
            "mae": round(self.position.mae, 2),
            "mfe": round(self.position.mfe, 2),
            "mae_pct": round(self.position.mae / (self.position.entry_price * self.position.original_quantity) * 100, 2) if self.position.original_quantity > 0 else 0.0,
            "mfe_pct": round(self.position.mfe / (self.position.entry_price * self.position.original_quantity) * 100, 2) if self.position.original_quantity > 0 else 0.0,
            "bars_held": max(0, current_bar_count - self.position.entry_bar_count) if current_bar_count > 0 else None,
        }
        self.closed_trades.append(trade)

        self.position.quantity -= quantity
        logger.info(
            f"Paper PARTIAL CLOSE {quantity} of {self.position.direction} "
            f"{self.position.symbol} @ {fill_price:.2f} | P&L: ${pnl:.2f} | "
            f"Remaining: {self.position.quantity} | Reason: {reason}"
        )
        return trade

    @property
    def equity(self) -> float:
        return self.capital

    def total_equity(self, current_price: float) -> float:
        """Capital + unrealized P&L (mark-to-market)."""
        if self.position is None:
            return self.capital
        return self.capital + self.position.unrealized_pnl(current_price)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_capital <= 0:
            return 0.0
        return (self.peak_capital - self.capital) / self.peak_capital

    @property
    def daily_pnl(self) -> float:
        today = datetime.now(ET).date()
        # Only scan recent trades (reverse iterate, stop at first different day)
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
