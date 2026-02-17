"""Paper trading engine - simulated order execution."""

from __future__ import annotations
from datetime import datetime
from typing import Optional
import logging
import random

logger = logging.getLogger(__name__)


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
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.strategy = strategy
        self.highest_since_entry = entry_price
        self.lowest_since_entry = entry_price

    def update_extremes(self, high: float, low: float):
        self.highest_since_entry = max(self.highest_since_entry, high)
        self.lowest_since_entry = min(self.lowest_since_entry, low)

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

    def _apply_slippage(self, price: float, is_buy: bool) -> float:
        slip = price * (self.slippage_bps / 10000)
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
    ) -> Optional[PaperPosition]:
        if self.position is not None:
            logger.warning("Already have an open position")
            return None

        is_buy = direction == "LONG"
        fill_price = self._apply_slippage(price, is_buy)

        self.position = PaperPosition(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=fill_price,
            entry_time=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
        )

        logger.info(
            f"Paper {direction} {quantity} {symbol} @ {fill_price:.2f} "
            f"(slippage from {price:.2f})"
        )
        return self.position

    def close_position(
        self, price: float, reason: str = "manual"
    ) -> Optional[dict]:
        if self.position is None:
            return None

        is_sell = self.position.direction == "LONG"
        fill_price = self._apply_slippage(price, not is_sell)

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
            "exit_time": datetime.now().isoformat(),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (self.position.entry_price * self.position.quantity) * 100, 2),
            "exit_reason": reason,
            "strategy": self.position.strategy,
        }
        self.closed_trades.append(trade)

        logger.info(
            f"Paper CLOSE {self.position.direction} {self.position.quantity} "
            f"{self.position.symbol} @ {fill_price:.2f} | P&L: ${pnl:.2f} | "
            f"Reason: {reason}"
        )
        self.position = None
        return trade

    @property
    def equity(self) -> float:
        return self.capital

    @property
    def drawdown_pct(self) -> float:
        if self.peak_capital <= 0:
            return 0.0
        return (self.peak_capital - self.capital) / self.peak_capital

    @property
    def daily_pnl(self) -> float:
        today = datetime.now().date()
        return sum(
            t["pnl"] for t in self.closed_trades
            if datetime.fromisoformat(t["exit_time"]).date() == today
        )

    @property
    def trades_today(self) -> int:
        today = datetime.now().date()
        return sum(
            1 for t in self.closed_trades
            if datetime.fromisoformat(t["exit_time"]).date() == today
        )
