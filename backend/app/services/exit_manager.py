"""Centralized exit management: scaling out, adaptive trailing, breakeven stops."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import logging

from app.services.strategies.base import (
    ExitReason,
    ExitSignal,
    ScaleLevel,
    Direction,
)

logger = logging.getLogger(__name__)

# Strategy exit reasons that ExitManager passes through unchanged.
# Price-based exits (trailing_stop, take_profit, stop_loss) are now
# handled by ExitManager's own logic so they are NOT in this set.
_STRATEGY_PASSTHROUGH_REASONS = frozenset({
    ExitReason.EOD,
    ExitReason.TIME_STOP,
    ExitReason.REVERSE_SIGNAL,
    ExitReason.FALSE_BREAKOUT,
})

# Default scale-out levels
DEFAULT_SCALES: list[ScaleLevel] = [
    ScaleLevel(
        pct_to_close=0.50,
        atr_profit_multiple=1.0,
        move_stop_to_breakeven=True,
        new_trailing_atr_mult=None,
    ),
    ScaleLevel(
        pct_to_close=0.25,
        atr_profit_multiple=2.0,
        move_stop_to_breakeven=False,
        new_trailing_atr_mult=0.5,
    ),
]


@dataclass
class PositionState:
    """Snapshot of position state passed into ExitManager."""
    direction: str
    entry_price: float
    quantity: int
    original_quantity: int
    scales_completed: list[int]
    effective_stop: float
    trailing_atr_mult: Optional[float]
    highest_since_entry: float
    lowest_since_entry: float


class ExitManager:
    """Handles scale-out targets, adaptive trailing stops, and breakeven stops.

    Sits between the trading engine and individual strategies. Strategies keep
    their existing should_exit() logic unchanged — ExitManager adds exit logic
    on top.
    """

    def __init__(self, scales: Optional[list[ScaleLevel]] = None):
        self.scales = scales or DEFAULT_SCALES

    def check_exit(
        self,
        state: PositionState,
        current_price: float,
        atr: float,
        strategy_exit: Optional[ExitSignal] = None,
        df=None,
        idx: Optional[int] = None,
    ) -> Optional[ExitSignal]:
        """Check exit conditions in priority order.

        Args:
            state: Current position state snapshot.
            current_price: Latest bar close price.
            atr: Current ATR value for the instrument.
            strategy_exit: Exit signal from the strategy's should_exit(), if any.
            df: DataFrame with indicators (for momentum exhaustion check).
            idx: Current bar index in df.

        Returns:
            ExitSignal if an exit/scale-out should occur, None otherwise.
        """
        if atr <= 0:
            return strategy_exit

        # 1. Check scale-out targets (one per call max)
        scale_signal = self._check_scale_out(state, current_price, atr)
        if scale_signal is not None:
            return scale_signal

        # 2. Check adaptive trailing stop
        trailing_signal = self._check_adaptive_trailing(state, current_price, atr)
        if trailing_signal is not None:
            return trailing_signal

        # 2b. Check momentum exhaustion
        if df is not None and idx is not None:
            momentum_signal = self.check_momentum_exhaustion(state, df, idx)
            if momentum_signal is not None:
                return momentum_signal

        # 3. Check effective stop (may be breakeven after scale-out)
        stop_signal = self._check_effective_stop(state, current_price)
        if stop_signal is not None:
            return stop_signal

        # 4. Delegate to strategy for non-price exits only (EOD, time stop,
        #    reverse signal, false breakout).  The ExitManager now owns
        #    trailing stop, take profit, and stop loss via the logic above.
        if strategy_exit and strategy_exit.reason in _STRATEGY_PASSTHROUGH_REASONS:
            return strategy_exit

        return None

    def compute_position_updates(
        self,
        state: PositionState,
        current_price: float,
        atr: float,
    ) -> dict:
        """Compute updates to position tracking fields (stop, trailing mult, extremes).

        Called every bar to update effective_stop and trailing_atr_mult even when
        no exit fires. Returns dict of fields to update on the position.
        """
        updates: dict = {}

        if atr <= 0:
            return updates

        profit = self._profit_in_price(state, current_price)
        profit_atr = profit / atr

        # Update trailing ATR multiplier based on profit level
        new_mult = self._adaptive_trailing_mult(profit_atr, state.trailing_atr_mult)
        if new_mult != state.trailing_atr_mult:
            updates["trailing_atr_mult"] = new_mult

        # Update effective stop if trailing tightens it
        if new_mult is not None:
            trailing_stop = self._compute_trailing_stop(
                state, current_price, atr, new_mult
            )
            if trailing_stop is not None:
                if state.direction == "LONG":
                    if trailing_stop > state.effective_stop:
                        updates["effective_stop"] = trailing_stop
                else:
                    if trailing_stop < state.effective_stop:
                        updates["effective_stop"] = trailing_stop

        return updates

    def _check_scale_out(
        self, state: PositionState, current_price: float, atr: float
    ) -> Optional[ExitSignal]:
        """Check if next scale-out level is triggered."""
        # Skip scaling if position too small to split meaningfully
        if state.original_quantity < 4:
            return None

        profit = self._profit_in_price(state, current_price)
        profit_atr = profit / atr

        for i, scale in enumerate(self.scales):
            scale_num = i + 1
            if scale_num in state.scales_completed:
                continue

            if profit_atr >= scale.atr_profit_multiple:
                qty_to_close = max(1, int(state.original_quantity * scale.pct_to_close))
                # Don't close more than we have
                qty_to_close = min(qty_to_close, state.quantity - 1)
                if qty_to_close <= 0:
                    continue

                reason = ExitReason.SCALE_OUT_1 if scale_num == 1 else ExitReason.SCALE_OUT_2
                logger.info(
                    f"Scale {scale_num} triggered: profit={profit_atr:.2f} ATR, "
                    f"closing {qty_to_close} of {state.quantity}"
                )
                return ExitSignal(
                    reason=reason,
                    exit_price=current_price,
                    quantity=qty_to_close,
                )
        return None

    def get_post_scale_updates(
        self, state: PositionState, scale_num: int, atr: float
    ) -> dict:
        """Return position field updates to apply after a scale-out fires.

        Called by the trading engine after executing a partial close.
        """
        updates: dict = {"scales_completed": state.scales_completed + [scale_num]}

        if scale_num <= len(self.scales):
            scale = self.scales[scale_num - 1]
            if scale.move_stop_to_breakeven:
                # Add small buffer to avoid noise-triggered breakeven stops
                buffer = 0.02  # 2 cents
                if state.direction == "LONG":
                    updates["effective_stop"] = state.entry_price + buffer
                else:
                    updates["effective_stop"] = state.entry_price - buffer
                logger.info(f"Stop moved to breakeven @ {updates['effective_stop']:.2f} (with {buffer} buffer)")
            if scale.new_trailing_atr_mult is not None:
                updates["trailing_atr_mult"] = scale.new_trailing_atr_mult

        return updates

    def _check_adaptive_trailing(
        self, state: PositionState, current_price: float, atr: float
    ) -> Optional[ExitSignal]:
        """Check if price has hit the adaptive trailing stop."""
        if state.trailing_atr_mult is None:
            return None

        trailing_stop = self._compute_trailing_stop(
            state, current_price, atr, state.trailing_atr_mult
        )
        if trailing_stop is None:
            return None

        if state.direction == "LONG" and current_price <= trailing_stop:
            return ExitSignal(
                reason=ExitReason.ADAPTIVE_TRAILING,
                exit_price=current_price,
            )
        elif state.direction == "SHORT" and current_price >= trailing_stop:
            return ExitSignal(
                reason=ExitReason.ADAPTIVE_TRAILING,
                exit_price=current_price,
            )
        return None

    def _check_effective_stop(
        self, state: PositionState, current_price: float
    ) -> Optional[ExitSignal]:
        """Check if price has hit the effective stop (may be breakeven)."""
        if state.direction == "LONG" and current_price <= state.effective_stop:
            return ExitSignal(
                reason=ExitReason.STOP_LOSS,
                exit_price=current_price,
            )
        elif state.direction == "SHORT" and current_price >= state.effective_stop:
            return ExitSignal(
                reason=ExitReason.STOP_LOSS,
                exit_price=current_price,
            )
        return None

    def check_momentum_exhaustion(
        self, state: PositionState, df, idx: int
    ) -> Optional[ExitSignal]:
        """Exit if momentum is exhausting (RSI reversal from extreme)."""
        import pandas as pd

        if idx < 2:
            return None
        rsi = df.iloc[idx].get("rsi")
        prev_rsi = df.iloc[idx - 1].get("rsi")
        if rsi is None or prev_rsi is None:
            return None
        if isinstance(rsi, float) and pd.isna(rsi):
            return None
        if isinstance(prev_rsi, float) and pd.isna(prev_rsi):
            return None

        if state.direction == "LONG":
            # Was overbought, now dropping
            if prev_rsi > 70 and rsi < 65:
                return ExitSignal(
                    reason=ExitReason.TAKE_PROFIT,
                    exit_price=float(df.iloc[idx]["close"]),
                )
        else:
            # Was oversold, now rising
            if prev_rsi < 30 and rsi > 35:
                return ExitSignal(
                    reason=ExitReason.TAKE_PROFIT,
                    exit_price=float(df.iloc[idx]["close"]),
                )
        return None

    def _compute_trailing_stop(
        self,
        state: PositionState,
        current_price: float,
        atr: float,
        mult: float,
    ) -> Optional[float]:
        """Compute trailing stop price based on extreme since entry."""
        distance = atr * mult
        if state.direction == "LONG":
            return state.highest_since_entry - distance
        else:
            return state.lowest_since_entry + distance

    @staticmethod
    def _adaptive_trailing_mult(
        profit_atr: float, current_mult: Optional[float]
    ) -> Optional[float]:
        """Smooth adaptive trailing: tightens continuously as profit grows."""
        if profit_atr < 1.0:
            return current_mult
        # Smooth curve: starts at 1.0x ATR at 1 ATR profit, tightens to 0.3x ATR
        return max(0.3, 1.5 - 0.5 * profit_atr)

    @staticmethod
    def _profit_in_price(state: PositionState, current_price: float) -> float:
        """Raw profit in price terms (positive = in the money)."""
        if state.direction == "LONG":
            return current_price - state.entry_price
        return state.entry_price - current_price
