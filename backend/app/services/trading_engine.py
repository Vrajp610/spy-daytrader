"""Main async trading loop: regime detect -> strategy signal -> risk check -> execute."""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd

from app.config import settings
from app.services.data_manager import DataManager
from app.services.exit_manager import ExitManager, PositionState
from app.services.paper_engine import PaperEngine
from app.services.risk_manager import RiskManager
from app.services.strategies.base import BaseStrategy, Direction, TradeSignal, ExitReason
from app.services.strategies.regime_detector import RegimeDetector, MarketRegime
from app.services.strategies.vwap_reversion import VWAPReversionStrategy
from app.services.strategies.orb import ORBStrategy
from app.services.strategies.ema_crossover import EMACrossoverStrategy
from app.services.strategies.volume_flow import VolumeFlowStrategy
from app.services.strategies.mtf_momentum import MTFMomentumStrategy
from app.services.strategies.rsi_divergence import RSIDivergenceStrategy
from app.services.strategies.bb_squeeze import BBSqueezeStrategy
from app.services.strategies.macd_reversal import MACDReversalStrategy
from app.services.strategies.momentum_scalper import MomentumScalperStrategy
from app.services.strategies.gap_fill import GapFillStrategy
from app.services.strategies.micro_pullback import MicroPullbackStrategy
from app.services.strategies.double_bottom_top import DoubleBottomTopStrategy
from app.websocket import ws_manager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: ["orb", "ema_crossover", "mtf_momentum", "micro_pullback", "momentum_scalper"],
    MarketRegime.TRENDING_DOWN: ["orb", "ema_crossover", "mtf_momentum", "micro_pullback", "momentum_scalper"],
    MarketRegime.RANGE_BOUND: ["vwap_reversion", "volume_flow", "rsi_divergence", "bb_squeeze", "double_bottom_top"],
    MarketRegime.VOLATILE: ["vwap_reversion", "volume_flow", "macd_reversal", "gap_fill"],
}


class TradingEngine:
    """Main trading loop that runs as an async task."""

    def __init__(self):
        self.running = False
        self.mode = settings.trading_mode  # paper / live
        self.paper_engine = PaperEngine(settings.initial_capital)
        self.risk_manager = RiskManager()
        self.exit_manager = ExitManager()
        self.regime_detector = RegimeDetector()
        self.data_manager = DataManager()
        self.current_regime = MarketRegime.RANGE_BOUND

        self.strategies: dict[str, BaseStrategy] = {
            "vwap_reversion": VWAPReversionStrategy(),
            "orb": ORBStrategy(),
            "ema_crossover": EMACrossoverStrategy(),
            "volume_flow": VolumeFlowStrategy(),
            "mtf_momentum": MTFMomentumStrategy(),
            "rsi_divergence": RSIDivergenceStrategy(),
            "bb_squeeze": BBSqueezeStrategy(),
            "macd_reversal": MACDReversalStrategy(),
            "momentum_scalper": MomentumScalperStrategy(),
            "gap_fill": GapFillStrategy(),
            "micro_pullback": MicroPullbackStrategy(),
            "double_bottom_top": DoubleBottomTopStrategy(),
        }
        self.enabled_strategies: set[str] = set(self.strategies.keys())

        self._task: Optional[asyncio.Task] = None
        self._last_data_fetch: Optional[datetime] = None
        self._last_trading_date = None
        self._df_1min: Optional[pd.DataFrame] = None
        self._df_5min: Optional[pd.DataFrame] = None
        self._df_15min: Optional[pd.DataFrame] = None

    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Trading engine started in {self.mode} mode")
        await ws_manager.broadcast("status_update", {
            "running": True, "mode": self.mode,
        })

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Close any open position at last known market price
        if self.paper_engine.position:
            price = self.paper_engine.position.entry_price  # fallback
            if self._df_1min is not None and not self._df_1min.empty:
                price = float(self._df_1min.iloc[-1]["close"])
            trade = self.paper_engine.close_position(price, reason="bot_stopped")
            if trade:
                self.risk_manager.record_trade_result(trade["pnl"])
                await ws_manager.broadcast("trade_update", trade)

        logger.info("Trading engine stopped")
        await ws_manager.broadcast("status_update", {
            "running": False, "mode": self.mode,
        })

    def set_mode(self, mode: str):
        self.mode = mode
        settings.trading_mode = mode

    async def _run_loop(self):
        """Main trading loop - runs every 30 seconds during market hours."""
        while self.running:
            try:
                now = datetime.now(ET)
                t = now.time()

                # Only trade during market hours (9:30 AM - 4:00 PM ET)
                if t < time(9, 30) or t >= time(16, 0):
                    await asyncio.sleep(30)
                    continue

                # Reset risk manager at start of each new trading day
                today = now.date()
                if self._last_trading_date != today:
                    self.risk_manager.reset_daily()
                    self._last_trading_date = today

                # Fetch fresh data every 60 seconds
                if (self._last_data_fetch is None or
                        (now - self._last_data_fetch).total_seconds() >= 60):
                    await self._fetch_data()

                if self._df_1min is None or self._df_1min.empty:
                    await asyncio.sleep(30)
                    continue

                # Also compute 15-min bars for MTF strategy
                if self._df_1min is not None and not self._df_1min.empty:
                    self._df_15min = self.data_manager.resample_to_interval(self._df_1min, "15min")

                # Detect regime
                if self._df_5min is not None and len(self._df_5min) > 20:
                    self.current_regime = self.regime_detector.detect(
                        self._df_5min, len(self._df_5min) - 1
                    )

                # Broadcast price update
                last_bar = self._df_1min.iloc[-1]
                await ws_manager.broadcast("price_update", {
                    "price": float(last_bar["close"]),
                    "volume": int(last_bar["volume"]),
                    "regime": self.current_regime.value,
                    "timestamp": str(self._df_1min.index[-1]),
                })

                # Check exits
                if self.paper_engine.position:
                    await self._check_exits()

                # Check entries
                if not self.paper_engine.position:
                    await self._check_entries()

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trading loop error: {e}", exc_info=True)
                await ws_manager.broadcast("error", {"message": str(e)})
                await asyncio.sleep(30)

    async def _fetch_data(self):
        """Fetch latest intraday data (runs blocking I/O in thread pool)."""
        try:
            loop = asyncio.get_running_loop()
            self._df_1min = await loop.run_in_executor(
                None, lambda: self.data_manager.fetch_intraday("SPY", period="2d", interval="1m")
            )
            if not self._df_1min.empty:
                self._df_1min = self.data_manager.add_indicators(self._df_1min)
                self._df_5min = self.data_manager.resample_to_5min(self._df_1min)
            self._last_data_fetch = datetime.now(ET)
        except Exception as e:
            logger.error(f"Data fetch error: {e}")

    async def _check_entries(self):
        """Check all enabled strategies for entry signals."""
        can_trade, reason = self.risk_manager.can_trade(
            self.paper_engine.capital,
            self.paper_engine.peak_capital,
            self.paper_engine.daily_pnl,
            self.paper_engine.trades_today,
        )
        if not can_trade:
            logger.debug(f"Cannot trade: {reason}")
            return

        # Filter strategies by regime
        allowed = [
            s for s in self.enabled_strategies
            if s in REGIME_STRATEGY_MAP.get(self.current_regime, [])
        ]

        now = datetime.now(ET)
        for strat_name in allowed:
            strategy = self.strategies.get(strat_name)
            if not strategy:
                continue

            if strat_name == "ema_crossover" and self._df_5min is not None and len(self._df_5min) > 30:
                idx = len(self._df_5min) - 1
                signal = strategy.generate_signal(self._df_5min, idx, now)
            elif strat_name == "mtf_momentum":
                # MTF strategy needs all three timeframes
                if (self._df_1min is not None and len(self._df_1min) > 30
                        and self._df_5min is not None and len(self._df_5min) > 20
                        and self._df_15min is not None and len(self._df_15min) > 10):
                    idx = len(self._df_1min) - 1
                    signal = strategy.generate_signal(
                        self._df_1min, idx, now,
                        df_5min=self._df_5min, df_15min=self._df_15min,
                    )
                else:
                    continue
            elif self._df_1min is not None and len(self._df_1min) > 30:
                idx = len(self._df_1min) - 1
                signal = strategy.generate_signal(self._df_1min, idx, now)
            else:
                continue

            if signal:
                quantity = self.risk_manager.calculate_position_size(
                    signal, self.paper_engine.capital
                )
                if quantity <= 0:
                    continue

                if self.mode == "paper":
                    pos = self.paper_engine.open_position(
                        symbol="SPY",
                        direction=signal.direction.value,
                        quantity=quantity,
                        price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        strategy=strat_name,
                    )
                    if pos:
                        await ws_manager.broadcast("trade_update", {
                            "action": "OPEN",
                            "strategy": strat_name,
                            "direction": signal.direction.value,
                            "quantity": quantity,
                            "price": signal.entry_price,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                            "regime": self.current_regime.value,
                        })
                        break
                else:
                    # Live mode
                    from app.services.schwab_client import schwab_client
                    side = "BUY" if signal.direction == Direction.LONG else "SELL"
                    result = await schwab_client.place_order("SPY", quantity, side)
                    if result and result.get("status") == "FILLED":
                        self.paper_engine.open_position(
                            symbol="SPY",
                            direction=signal.direction.value,
                            quantity=quantity,
                            price=signal.entry_price,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                            strategy=strat_name,
                        )
                        await ws_manager.broadcast("trade_update", {
                            "action": "OPEN",
                            "strategy": strat_name,
                            "direction": signal.direction.value,
                            "quantity": quantity,
                            "price": signal.entry_price,
                            "live": True,
                        })
                        break

    async def _check_exits(self):
        """Check if open position should be closed or scaled out."""
        pos = self.paper_engine.position
        if not pos:
            return

        now = datetime.now(ET)

        # EOD exit at 3:55 PM - use last known market price
        if now.time() >= time(15, 55):
            eod_price = float(self._df_1min.iloc[-1]["close"]) if self._df_1min is not None and not self._df_1min.empty else pos.entry_price
            await self._close_position(eod_price, "eod")
            return

        strategy = self.strategies.get(pos.strategy)
        if not strategy:
            return

        if self._df_1min is None or self._df_1min.empty:
            return

        idx = len(self._df_1min) - 1
        current_price = float(self._df_1min.iloc[-1]["close"])

        # Update extremes
        pos.update_extremes(
            float(self._df_1min.iloc[-1]["high"]),
            float(self._df_1min.iloc[-1]["low"]),
        )

        # Get ATR for exit manager
        atr = float(self._df_1min.iloc[-1].get("atr", 0)) if "atr" in self._df_1min.columns else 0.0

        # Build strategy exit signal
        signal_proxy = TradeSignal(
            strategy=pos.strategy,
            direction=Direction(pos.direction),
            entry_price=pos.entry_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            quantity=pos.quantity,
        )

        strategy_exit = strategy.should_exit(
            self._df_1min, idx, signal_proxy,
            pos.entry_time, now,
            pos.highest_since_entry, pos.lowest_since_entry,
        )

        # Build PositionState for ExitManager
        state = PositionState(
            direction=pos.direction,
            entry_price=pos.entry_price,
            quantity=pos.quantity,
            original_quantity=pos.original_quantity,
            scales_completed=list(pos.scales_completed),
            effective_stop=pos.effective_stop,
            trailing_atr_mult=pos.trailing_atr_mult,
            highest_since_entry=pos.highest_since_entry,
            lowest_since_entry=pos.lowest_since_entry,
        )

        # Update position tracking (trailing stop tightening, etc.)
        if atr > 0:
            updates = self.exit_manager.compute_position_updates(state, current_price, atr)
            if "effective_stop" in updates:
                pos.effective_stop = updates["effective_stop"]
                state.effective_stop = updates["effective_stop"]
            if "trailing_atr_mult" in updates:
                pos.trailing_atr_mult = updates["trailing_atr_mult"]
                state.trailing_atr_mult = updates["trailing_atr_mult"]

        # Check exits via ExitManager
        exit_signal = self.exit_manager.check_exit(state, current_price, atr, strategy_exit)

        if exit_signal:
            if exit_signal.quantity is not None and exit_signal.quantity < pos.quantity:
                # Partial close (scale-out)
                await self._reduce_position(
                    exit_signal.exit_price,
                    exit_signal.quantity,
                    exit_signal.reason.value,
                )
                # Apply post-scale updates
                scale_num = 1 if exit_signal.reason == ExitReason.SCALE_OUT_1 else 2
                post_updates = self.exit_manager.get_post_scale_updates(state, scale_num, atr)
                if "scales_completed" in post_updates:
                    pos.scales_completed = post_updates["scales_completed"]
                if "effective_stop" in post_updates:
                    pos.effective_stop = post_updates["effective_stop"]
                if "trailing_atr_mult" in post_updates:
                    pos.trailing_atr_mult = post_updates["trailing_atr_mult"]
            else:
                # Full close
                await self._close_position(exit_signal.exit_price, exit_signal.reason.value)

    async def _close_position(self, price: float, reason: str):
        """Close position in paper or live mode."""
        if self.mode == "live":
            from app.services.schwab_client import schwab_client
            if schwab_client.is_configured:
                pos = self.paper_engine.position
                if pos:
                    side = "SELL" if pos.direction == "LONG" else "BUY"
                    await schwab_client.place_order("SPY", pos.quantity, side)

        trade = self.paper_engine.close_position(price, reason)
        if trade:
            self.risk_manager.record_trade_result(trade["pnl"])
            await ws_manager.broadcast("trade_update", {
                "action": "CLOSE",
                **trade,
            })

    async def _reduce_position(self, price: float, quantity: int, reason: str):
        """Partially close position (scale-out)."""
        if self.mode == "live":
            from app.services.schwab_client import schwab_client
            if schwab_client.is_configured:
                pos = self.paper_engine.position
                if pos:
                    side = "SELL" if pos.direction == "LONG" else "BUY"
                    await schwab_client.place_order("SPY", quantity, side)

        trade = self.paper_engine.reduce_position(quantity, price, reason)
        if trade:
            self.risk_manager.record_trade_result(trade["pnl"])
            await ws_manager.broadcast("trade_update", {
                "action": "PARTIAL_CLOSE",
                **trade,
            })

    def get_status(self) -> dict:
        pos = self.paper_engine.position
        open_pos = None
        if pos:
            # Use last known market price for unrealized P&L
            current_price = pos.entry_price
            if self._df_1min is not None and not self._df_1min.empty:
                current_price = float(self._df_1min.iloc[-1]["close"])
            open_pos = {
                "symbol": pos.symbol,
                "direction": pos.direction,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "strategy": pos.strategy,
                "unrealized_pnl": round(pos.unrealized_pnl(current_price), 2),
                "original_quantity": pos.original_quantity,
                "scales_completed": list(pos.scales_completed),
                "effective_stop": pos.effective_stop,
            }
        return {
            "running": self.running,
            "mode": self.mode,
            "current_regime": self.current_regime.value,
            "open_position": open_pos,
            "daily_pnl": round(self.paper_engine.daily_pnl, 2),
            "daily_trades": self.paper_engine.trades_today,
            "consecutive_losses": self.risk_manager.consecutive_losses,
            "cooldown_until": (
                self.risk_manager.cooldown_until.isoformat()
                if self.risk_manager.cooldown_until
                else None
            ),
            "equity": round(self.paper_engine.equity, 2),
            "peak_equity": round(self.paper_engine.peak_capital, 2),
            "drawdown_pct": round(self.paper_engine.drawdown_pct * 100, 2),
        }


# Singleton
trading_engine = TradingEngine()
