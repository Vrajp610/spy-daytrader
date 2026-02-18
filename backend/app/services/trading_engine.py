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
from app.services.schwab_client import schwab_client
from app.services.paper_engine import PaperEngine
from app.services.risk_manager import RiskManager
from app.services.strategies.base import BaseStrategy, Direction
from app.services.strategies.regime_detector import RegimeDetector, MarketRegime
from app.services.strategies.vwap_reversion import VWAPReversionStrategy
from app.services.strategies.orb import ORBStrategy
from app.services.strategies.ema_crossover import EMACrossoverStrategy
from app.services.strategies.volume_flow import VolumeFlowStrategy
from app.services.strategies.mtf_momentum import MTFMomentumStrategy
from app.websocket import ws_manager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: ["orb", "ema_crossover", "mtf_momentum"],
    MarketRegime.TRENDING_DOWN: ["orb", "ema_crossover", "mtf_momentum"],
    MarketRegime.RANGE_BOUND: ["vwap_reversion", "volume_flow"],
    MarketRegime.VOLATILE: ["vwap_reversion", "volume_flow"],
}


class TradingEngine:
    """Main trading loop that runs as an async task."""

    def __init__(self):
        self.running = False
        self.mode = settings.trading_mode  # paper / live
        self.paper_engine = PaperEngine(settings.initial_capital)
        self.risk_manager = RiskManager()
        self.regime_detector = RegimeDetector()
        self.data_manager = DataManager()
        self.current_regime = MarketRegime.RANGE_BOUND

        self.strategies: dict[str, BaseStrategy] = {
            "vwap_reversion": VWAPReversionStrategy(),
            "orb": ORBStrategy(),
            "ema_crossover": EMACrossoverStrategy(),
            "volume_flow": VolumeFlowStrategy(),
            "mtf_momentum": MTFMomentumStrategy(),
        }
        self.enabled_strategies: set[str] = set(self.strategies.keys())

        self._task: Optional[asyncio.Task] = None
        self._last_data_fetch: Optional[datetime] = None
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
            self._task = None

        # Close any open position
        if self.paper_engine.position:
            quote = await schwab_client.get_quote("SPY")
            price = quote["last"] if quote else self.paper_engine.position.entry_price
            trade = self.paper_engine.close_position(price, reason="bot_stopped")
            if trade:
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

                # Fetch fresh data every 60 seconds
                if (self._last_data_fetch is None or
                        (now - self._last_data_fetch).seconds >= 60):
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
        """Fetch latest intraday data."""
        try:
            self._df_1min = self.data_manager.fetch_intraday("SPY", period="2d", interval="1m")
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
        """Check if open position should be closed."""
        pos = self.paper_engine.position
        if not pos:
            return

        now = datetime.now(ET)

        # EOD exit at 3:55 PM
        if now.time() >= time(15, 55):
            await self._close_position(pos.entry_price, "eod")  # use last price
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

        from app.services.strategies.base import TradeSignal, Direction
        signal_proxy = TradeSignal(
            strategy=pos.strategy,
            direction=Direction(pos.direction),
            entry_price=pos.entry_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            quantity=pos.quantity,
        )

        exit_signal = strategy.should_exit(
            self._df_1min, idx, signal_proxy,
            pos.entry_time, now,
            pos.highest_since_entry, pos.lowest_since_entry,
        )

        if exit_signal:
            await self._close_position(exit_signal.exit_price, exit_signal.reason.value)

    async def _close_position(self, price: float, reason: str):
        """Close position in paper or live mode."""
        if self.mode == "live" and schwab_client.is_configured:
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

    def get_status(self) -> dict:
        pos = self.paper_engine.position
        return {
            "running": self.running,
            "mode": self.mode,
            "current_regime": self.current_regime.value,
            "open_position": {
                "symbol": pos.symbol,
                "direction": pos.direction,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "strategy": pos.strategy,
                "unrealized_pnl": round(
                    pos.unrealized_pnl(pos.entry_price), 2  # placeholder
                ),
            } if pos else None,
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
