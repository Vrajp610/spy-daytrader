"""Main async trading loop: regime detect -> strategy signal -> options select -> execute."""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd

from app.config import settings
from app.services.data_manager import DataManager
from app.services.options.chain_provider import OptionChainProvider
from app.services.options.models import (
    OptionsStrategyType, OPTIONS_EXIT_RULES, STRATEGY_ABBREV,
    OptionChainSnapshot,
)
from app.services.options.paper_options_engine import PaperOptionsEngine
from app.services.options.selector import OptionsSelector
from app.services.options import sizing as options_sizing
from app.services.risk_manager import RiskManager
from app.services.strategies.base import BaseStrategy, Direction, TradeSignal, MarketContext
from app.services.strategies.regime_detector import RegimeDetector, MarketRegime
from app.services.strategies.vwap_reversion import VWAPReversionStrategy
from app.services.strategies.ema_crossover import EMACrossoverStrategy
from app.services.strategies.mtf_momentum import MTFMomentumStrategy
from app.services.strategies.adx_trend import ADXTrendStrategy
from app.services.strategies.keltner_breakout import KeltnerBreakoutStrategy
from app.services.strategies.rsi2_mean_reversion import RSI2MeanReversionStrategy
from app.services.strategies.theta_decay import ThetaDecayStrategy
from app.services.strategies.smc_ict import SMCICTStrategy
from app.services.strategies.orb_scalp import ORBScalpStrategy
from app.services.strategies.trend_continuation import TrendContinuationStrategy
from app.services.strategies.zero_dte_bull_put import ZeroDTEBullPutStrategy
from app.services.strategies.vol_spike import VolSpikeStrategy
from app.services.strategy_monitor import strategy_monitor
from app.services.event_calendar import macro_calendar
from app.services.news_scanner import news_scanner
from app.services.trade_memory import query_similar_trades
from app.services.trade_advisor import assess_trade as advisor_assess
from app.websocket import ws_manager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: [
        "ema_crossover", "mtf_momentum", "adx_trend", "rsi2_mean_reversion",
        "smc_ict", "theta_decay", "orb_scalp", "trend_continuation",
    ],
    MarketRegime.TRENDING_DOWN: [
        "ema_crossover", "mtf_momentum", "adx_trend", "rsi2_mean_reversion",
        "smc_ict", "theta_decay", "orb_scalp", "trend_continuation",
    ],
    MarketRegime.RANGE_BOUND: [
        "vwap_reversion", "rsi2_mean_reversion", "smc_ict", "theta_decay",
        "zero_dte_bull_put",
    ],
    MarketRegime.VOLATILE: [
        "vwap_reversion", "keltner_breakout",
        # smc_ict + theta_decay excluded: VOLATILE skipped inside each strategy
        # vol_spike buys straddles to profit from IV expansion
        "vol_spike",
    ],
}

# Minimum blended composite score to allow a strategy to trade
# (calibrated from live losses: momentum_scalper=-6.5, adx_trend=-8.2 both lost)
MIN_COMPOSITE_SCORE_TO_TRADE = 5.0


class TradingEngine:
    """Main trading loop — options-only execution."""

    def __init__(self):
        self.running = False
        self.mode = settings.trading_mode  # paper / live
        self.paper_engine = PaperOptionsEngine(settings.initial_capital)
        self.risk_manager = RiskManager()
        self.options_selector = OptionsSelector()
        self.chain_provider = OptionChainProvider()
        self.regime_detector = RegimeDetector()
        self.data_manager = DataManager()
        self.current_regime = MarketRegime.RANGE_BOUND

        self.strategies: dict[str, BaseStrategy] = {
            "vwap_reversion":      VWAPReversionStrategy(),
            "ema_crossover":       EMACrossoverStrategy(),
            "mtf_momentum":        MTFMomentumStrategy(),
            "adx_trend":           ADXTrendStrategy(),
            "keltner_breakout":    KeltnerBreakoutStrategy(),
            "rsi2_mean_reversion": RSI2MeanReversionStrategy(),
            "theta_decay":         ThetaDecayStrategy(),
            "smc_ict":             SMCICTStrategy(),
            "orb_scalp":           ORBScalpStrategy(),
            "trend_continuation":  TrendContinuationStrategy(),
            "zero_dte_bull_put":   ZeroDTEBullPutStrategy(),
            "vol_spike":           VolSpikeStrategy(),
        }
        self.enabled_strategies: set[str] = set(self.strategies.keys())

        self._task: Optional[asyncio.Task] = None
        self._last_data_fetch: Optional[datetime] = None
        self._last_extended_fetch: Optional[datetime] = None
        self._last_chain_fetch: Optional[datetime] = None
        self._last_trading_date = None
        self._df_1min: Optional[pd.DataFrame] = None
        self._df_5min: Optional[pd.DataFrame] = None
        self._df_15min: Optional[pd.DataFrame] = None
        self._df_30min: Optional[pd.DataFrame] = None
        self._df_1hr: Optional[pd.DataFrame] = None
        self._df_4hr: Optional[pd.DataFrame] = None
        self._current_chain: Optional[OptionChainSnapshot] = None
        self._strategy_scores: dict[str, float] = {}
        self._scores_last_refresh: float = 0.0
        self._state_restored: bool = False  # guard: restore equity from DB only once per process

        # VIX macro regime inputs (fetched every 5 min via yfinance)
        self._current_vix: float = 20.0          # VIX spot (^VIX)
        self._current_vix3m: float = 22.0        # 3-month VIX (^VIX3M)
        self._vix_term_ratio: float = 0.91       # VIX/VIX3M; <1=contango, >1=backwardation
        self._vix_last_fetch: Optional[datetime] = None
        self._calendar_last_fetch: Optional[datetime] = None  # macro event calendar + news refresh

        # QQQ correlated data for SMT divergence checks in smc_ict
        self._df_qqq_5min:  Optional[pd.DataFrame] = None
        self._df_qqq_15min: Optional[pd.DataFrame] = None

    async def _restore_paper_state(self):
        """Restore paper engine capital from persisted trade history.

        On server restart, capital would otherwise reset to initial_capital.
        We sum all closed paper trade P&L from the DB so equity is continuous
        across restarts.  Also loads recent trades for daily P&L tracking.
        """
        try:
            from app.database import async_session
            from app.models import Trade as TradeModel
            from sqlalchemy import select, func

            async with async_session() as db:
                # Total realised P&L across all paper trades
                stmt = select(func.sum(TradeModel.pnl)).where(
                    TradeModel.is_paper.is_(True),
                    TradeModel.status == "CLOSED",
                    TradeModel.pnl.isnot(None),
                )
                result = await db.execute(stmt)
                total_pnl = result.scalar() or 0.0

                # Load ALL closed trades for daily P&L / daily trade count
                stmt2 = (
                    select(TradeModel)
                    .where(TradeModel.is_paper.is_(True), TradeModel.status == "CLOSED")
                    .order_by(TradeModel.exit_time.desc())
                )
                result2 = await db.execute(stmt2)
                recent_rows = result2.scalars().all()

            restored_capital = self.paper_engine.initial_capital + total_pnl
            self.paper_engine.capital = restored_capital
            self.paper_engine._last_equity = restored_capital
            self.paper_engine._peak_equity = max(
                self.paper_engine.initial_capital, restored_capital
            )

            # Populate in-memory closed_trades (oldest first) for daily helpers
            self.paper_engine.closed_trades = [
                {
                    "exit_time": (t.exit_time.isoformat() if t.exit_time else ""),
                    "pnl": t.pnl or 0.0,
                }
                for t in reversed(recent_rows)
            ]

            logger.info(
                f"Paper engine state restored: capital=${restored_capital:.2f} "
                f"(initial=${self.paper_engine.initial_capital:.2f}, "
                f"total_pnl=${total_pnl:.2f}, {len(recent_rows)} trades loaded)"
            )
        except Exception as e:
            logger.error(f"Could not restore paper engine state from DB: {e}")

    async def start(self):
        if self.running:
            return
        # Restore paper capital from DB on first start per process so equity
        # never resets to $25k after a server restart.
        if not self._state_restored:
            await self._restore_paper_state()
            self._state_restored = True
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Trading engine started in {self.mode} mode (OPTIONS)")
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
            price = self._get_last_price() or self.paper_engine.position.entry_underlying
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
        """Main trading loop - runs every 5 seconds during market hours."""
        while self.running:
            try:
                now = datetime.now(ET)
                t = now.time()

                # Only trade during market hours (9:30 AM - 4:00 PM ET)
                if t < time(9, 30) or t >= time(16, 0):
                    await asyncio.sleep(5)
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

                # Warn if data is stale
                if self._last_data_fetch and (now - self._last_data_fetch).total_seconds() > 120:
                    logger.warning(f"Data is {(now - self._last_data_fetch).total_seconds():.0f}s old")

                if self._df_1min is None or self._df_1min.empty:
                    await asyncio.sleep(5)
                    continue

                # Also compute 15-min bars for MTF strategy
                if self._df_1min is not None and not self._df_1min.empty:
                    self._df_15min = self.data_manager.resample_to_interval(self._df_1min, "15min")

                # Detect regime
                if self._df_5min is not None and len(self._df_5min) > 20:
                    self.current_regime = self.regime_detector.detect(
                        self._df_5min, len(self._df_5min) - 1
                    )

                # Fetch option chain every 60 seconds
                if (self._last_chain_fetch is None or
                        (now - self._last_chain_fetch).total_seconds() >= 60):
                    await self._fetch_chain()

                # Fetch VIX macro regime inputs (every 5 min, non-blocking)
                await self._fetch_vix_data()

                # Refresh macro event calendar + news scanner (every hour, non-blocking)
                await self._fetch_calendar_news()

                # Refresh strategy leaderboard scores
                await self._refresh_strategy_scores()

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

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trading loop error: {e}", exc_info=True)
                await ws_manager.broadcast("error", {"message": str(e)})
                await asyncio.sleep(5)

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

            # Fetch extended TF data every 15 minutes
            now = datetime.now(ET)
            if (self._last_extended_fetch is None
                    or (now - self._last_extended_fetch).total_seconds() >= 900):
                ext = await loop.run_in_executor(
                    None, lambda: self.data_manager.fetch_extended_data("SPY")
                )
                if ext:
                    self._df_30min = ext.get("df_30min")
                    self._df_1hr = ext.get("df_1hr")
                    self._df_4hr = ext.get("df_4hr")
                    self._last_extended_fetch = now

                # Fetch QQQ for SMT divergence alongside SPY (same 15-min window)
                try:
                    qqq_1min = await loop.run_in_executor(
                        None,
                        lambda: self.data_manager.fetch_intraday("QQQ", period="2d", interval="1m"),
                    )
                    if qqq_1min is not None and not qqq_1min.empty:
                        qqq_1min = self.data_manager.add_indicators(qqq_1min)
                        self._df_qqq_5min  = self.data_manager.resample_to_5min(qqq_1min)
                        self._df_qqq_15min = self.data_manager.resample_to_interval(qqq_1min, "15min")
                except Exception as qqq_err:
                    logger.debug(f"QQQ fetch error (non-critical): {qqq_err}")
        except Exception as e:
            logger.error(f"Data fetch error: {e}")

    async def _fetch_chain(self):
        """Fetch option chain data."""
        try:
            price = self._get_last_price()
            atr = 2.0
            if self._df_1min is not None and not self._df_1min.empty:
                atr_val = self._df_1min.iloc[-1].get("atr", 2.0)
                if atr_val and not pd.isna(atr_val):
                    atr = float(atr_val)

            self._current_chain = await self.chain_provider.get_chain(
                "SPY", underlying_price=price, atr=atr, bar_minutes=1,
            )
            self._last_chain_fetch = datetime.now(ET)

            if self._current_chain:
                logger.info(
                    f"Fetched option chain: {len(self._current_chain.calls)} calls, "
                    f"{len(self._current_chain.puts)} puts, "
                    f"IV rank: {self._current_chain.iv_rank:.0f}"
                )
        except Exception as e:
            logger.error(f"Chain fetch error: {e}")

    async def _fetch_vix_data(self):
        """Fetch VIX spot and VIX3M for macro regime gates (every 5 minutes).

        VIX > 35          → block ALL new entries (extreme fear, signals unreliable)
        VIX 25-35         → reduce sizes, prefer mean-reversion only
        VIX/VIX3M > 1.0  → backwardation = stress regime, cap to 2 trades/day
        VIX < 18          → calm/positive GEX proxy, allow momentum
        """
        now = datetime.now(ET)
        if self._vix_last_fetch and (now - self._vix_last_fetch).total_seconds() < 300:
            return  # refresh every 5 minutes

        try:
            import yfinance as yf
            loop = asyncio.get_running_loop()

            def _download():
                vix_data = yf.download(
                    "^VIX ^VIX3M", period="2d", interval="5m",
                    progress=False, auto_adjust=True,
                )
                return vix_data

            data = await loop.run_in_executor(None, _download)

            if data is not None and not data.empty:
                # Handle MultiIndex columns (ticker, field)
                close_cols = [c for c in data.columns if c[0].lower() == "close"]
                vix_col = next((c for c in close_cols if "VIX" in c[1] and "3M" not in c[1]), None)
                vix3m_col = next((c for c in close_cols if "VIX3M" in c[1]), None)

                if vix_col and not data[vix_col].dropna().empty:
                    self._current_vix = float(data[vix_col].dropna().iloc[-1])
                if vix3m_col and not data[vix3m_col].dropna().empty:
                    self._current_vix3m = float(data[vix3m_col].dropna().iloc[-1])

                if self._current_vix3m > 0:
                    self._vix_term_ratio = self._current_vix / self._current_vix3m

                self.risk_manager.set_vix(self._current_vix, self._current_vix3m)
                self._vix_last_fetch = now

                term_label = "BACKWARDATION" if self._vix_term_ratio > 1.0 else "contango"
                logger.info(
                    f"VIX: {self._current_vix:.1f} | VIX3M: {self._current_vix3m:.1f} | "
                    f"ratio: {self._vix_term_ratio:.3f} ({term_label})"
                )
        except Exception as e:
            logger.debug(f"VIX fetch error (non-critical): {e}")

    async def _fetch_calendar_news(self):
        """Refresh macro event calendar and news scanner (throttled to once per hour).

        The event calendar fetches ForexFactory this/next-week JSON (12h TTL).
        The news scanner fetches yfinance SPY/VIX headlines (4h TTL).
        Both singletons manage their own TTL internally; this method just
        rate-limits the async wakeup to avoid hitting the executors every 5s.
        """
        now = datetime.now(ET)
        if (self._calendar_last_fetch
                and (now - self._calendar_last_fetch).total_seconds() < 3600):
            return

        try:
            await macro_calendar.ensure_fresh()
            await news_scanner.ensure_fresh()
            self._calendar_last_fetch = now

            # Log upcoming events once per refresh
            upcoming = macro_calendar.upcoming_events(days_ahead=7)
            if upcoming:
                names = " | ".join(
                    f"{e['date']} {e['title']}" for e in upcoming[:4]
                )
                logger.info(f"Upcoming macro events (7d): {names}")
            else:
                logger.debug("No high-impact macro events in the next 7 days")

            risk = news_scanner.get_daily_risk()
            if risk != "LOW":
                reasons = news_scanner.get_risk_reasons()
                logger.info(
                    f"News risk={risk} | {'; '.join(r[:50] for r in reasons[:2])}"
                )
        except Exception as e:
            logger.debug(f"Calendar/news refresh error (non-critical): {e}")

    async def _refresh_strategy_scores(self):
        """Load strategy composite scores from leaderboard for performance weighting.
        Also checks if any auto-disabled strategies are eligible for re-enable.
        """
        import time
        now = time.time()
        if now - self._scores_last_refresh < 300:  # refresh every 5 minutes
            return
        self._scores_last_refresh = now
        try:
            from app.database import async_session
            from app.models import StrategyRanking
            from sqlalchemy import select
            async with async_session() as db:
                stmt = select(StrategyRanking)
                result = await db.execute(stmt)
                rankings = result.scalars().all()
                self._strategy_scores = {
                    r.strategy_name: r.composite_score
                    for r in rankings
                }
                # Check re-enable for any auto-disabled strategies
                for strat_name, backtest_score in self._strategy_scores.items():
                    reenabled = await strategy_monitor.check_and_reenable(
                        strat_name, backtest_score, db
                    )
                    if reenabled:
                        self.enabled_strategies.add(strat_name)
                        logger.info(f"Strategy {strat_name} re-enabled by monitor")
                        await ws_manager.broadcast("status_update", {
                            "strategy_reenabled": strat_name
                        })

            if self._strategy_scores:
                top = sorted(self._strategy_scores.items(), key=lambda x: x[1], reverse=True)[:3]
                logger.info(f"Strategy scores loaded: top 3 = {[(n, f'{s:.1f}') for n, s in top]}")
        except Exception as e:
            logger.debug(f"Could not load strategy scores: {e}")

    async def execute_webhook_signal(
        self,
        action: str,
        strategy: str,
        price: float,
        stop_loss: float,
        take_profit: float,
        confidence: float = 0.70,
        metadata: dict | None = None,
    ) -> dict:
        """Execute a pre-formed signal arriving from a TradingView webhook.

        Applies risk gates (daily loss, circuit breaker, existing position, no chain)
        but skips strategy filtering, confluence scoring, and trade advisor.
        """
        metadata = metadata or {}

        # CLOSE action — close any open position at the webhook price
        if action == "CLOSE":
            if self.paper_engine.position:
                await self._close_options_position(price, reason="webhook_close")
                return {"status": "closed", "reason": "position closed via webhook"}
            return {"status": "rejected", "reason": "no open position to close"}

        # Only BUY / SELL accepted beyond this point
        if action not in ("BUY", "SELL"):
            return {"status": "rejected", "reason": f"unknown action '{action}'"}

        # Risk gate
        equity = self.paper_engine.total_equity(price or self._get_last_price())
        can_trade, gate_reason = self.risk_manager.can_trade(
            equity,
            self.paper_engine.peak_equity,
            self.paper_engine.daily_pnl,
            self.paper_engine.trades_today,
        )
        if not can_trade:
            return {"status": "rejected", "reason": gate_reason}

        # Must not already be in a trade
        if self.paper_engine.position is not None:
            return {"status": "rejected", "reason": "position already open"}

        # Options chain must be loaded (market must be open)
        if self._current_chain is None:
            return {"status": "rejected", "reason": "no options chain available"}

        direction = Direction.LONG if action == "BUY" else Direction.SHORT

        # Build a synthetic TradeSignal from the webhook payload
        signal = TradeSignal(
            strategy=strategy,
            direction=direction,
            entry_price=price,
            stop_loss=stop_loss if stop_loss else price * 0.995,
            take_profit=take_profit if take_profit else price * 1.010,
            confidence=min(1.0, max(0.0, confidence)),
            metadata=metadata,
        )

        # Apply Kelly-adjusted risk fraction (VIX-adjusted, no confidence scaling)
        risk_fraction = self.risk_manager._kelly_risk_fraction()

        order = self.options_selector.select(
            signal, self.current_regime, self._current_chain,
            self.paper_engine.capital, risk_fraction,
        )
        if order is None:
            return {"status": "rejected", "reason": "options selector returned no order"}

        # Contract sizing
        open_risk = self.paper_engine.open_risk
        contracts = options_sizing.calculate_contracts(
            order, self.paper_engine.capital, risk_fraction, open_risk,
        )
        if contracts <= 0:
            return {"status": "rejected", "reason": "contract sizing returned 0"}

        old_contracts = max(1, order.contracts)
        for leg in order.legs:
            leg.quantity = contracts
        order.contracts = contracts
        order.max_loss = (order.max_loss / old_contracts) * contracts
        order.max_profit = (order.max_profit / old_contracts) * contracts
        order.regime = self.current_regime.value

        # Commission sanity check
        total_legs = sum(leg.quantity for leg in order.legs)
        estimated_commission = total_legs * settings.options_commission_per_contract * 2
        total_premium = abs(order.net_premium) * contracts * 100
        if estimated_commission >= total_premium * 0.5:
            return {"status": "rejected", "reason": "commission exceeds 50% of premium"}

        if order.is_credit and abs(order.net_premium) < 0.10:
            return {"status": "rejected", "reason": "credit premium too low (<$0.10)"}

        pos = self.paper_engine.open_position(order)
        if not pos:
            return {"status": "rejected", "reason": "paper engine rejected position"}

        trade_summary = {
            "strategy": strategy,
            "direction": direction.value,
            "option_strategy_type": order.strategy_type.value,
            "contracts": contracts,
            "net_premium": round(order.net_premium, 4),
            "max_loss": round(order.max_loss, 2),
            "max_profit": round(order.max_profit, 2),
            "display": order.to_display_string(),
        }
        await ws_manager.broadcast("trade_update", {
            "action": "OPEN",
            "source": "webhook",
            **trade_summary,
        })
        logger.info(
            f"Webhook signal executed: {strategy} {action} "
            f"{contracts}x {order.strategy_type.value} | {order.to_display_string()}"
        )
        return {"status": "executed", "order": trade_summary}

    async def _check_entries(self):
        """Check all enabled strategies for entry signals, then map to options."""
        last_price = self._get_last_price()
        equity = self.paper_engine.total_equity(last_price)
        can_trade, reason = self.risk_manager.can_trade(
            equity,
            self.paper_engine.peak_equity,   # mark-to-market peak (not stale free-cash peak)
            self.paper_engine.daily_pnl,
            self.paper_engine.trades_today,
        )
        if not can_trade:
            logger.debug(f"Cannot trade: {reason}")
            return

        # ── VIX macro gates (hedge fund: never fight extreme fear) ─────────
        if self._current_vix >= 35.0:
            logger.info(f"VIX={self._current_vix:.1f} ≥ 35 — all entries blocked (extreme fear)")
            return

        if self._current_chain is None:
            logger.debug("No option chain available, skipping entry check")
            return

        now = datetime.now(ET)
        today = now.date()

        # ── Macro event calendar gate ─────────────────────────────────────────
        # Block ALL entries on high-impact event days (FOMC, CPI, NFP, etc.).
        # Volatility and direction are unpredictable in the hours around releases.
        if macro_calendar.is_event_day(today):
            events = macro_calendar.get_events_for_date(today)
            names = ", ".join(e["title"] for e in events)
            logger.info(f"EVENT DAY ({names}) — all new entries blocked")
            return

        # ── News risk gate ────────────────────────────────────────────────────
        # Block all entries when news scanner detects HIGH macro shock language.
        news_risk = news_scanner.get_daily_risk()
        if news_risk == "HIGH":
            reasons = news_scanner.get_risk_reasons()
            logger.info(
                f"News risk=HIGH — all entries blocked | "
                f"{'; '.join(r[:60] for r in reasons[:2])}"
            )
            return

        # When VIX is elevated (25-35), restrict to mean-reversion strategies only.
        # High VIX → negative GEX environment → dealers amplify moves → momentum fails.
        vix_stressed = self._current_vix >= 25.0
        vix_backwardation = self._vix_term_ratio >= 1.0  # VIX > VIX3M = near-term fear spike

        # Filter strategies by regime AND auto-disable status
        allowed_by_regime = REGIME_STRATEGY_MAP.get(self.current_regime, [])

        # In stressed VIX environments, only allow mean-reversion strategies
        MEAN_REVERSION_STRATEGIES = {
            "vwap_reversion", "rsi2_mean_reversion",
        }
        if vix_stressed:
            allowed_by_regime = [s for s in allowed_by_regime if s in MEAN_REVERSION_STRATEGIES]
            if not allowed_by_regime:
                logger.debug(f"VIX={self._current_vix:.1f} stressed — no mean-reversion strategies in current regime")
                return

        allowed = [
            s for s in self.enabled_strategies
            if s in allowed_by_regime
            and not strategy_monitor.is_auto_disabled(s)
        ]

        # theta_decay holds spreads for 3 days — block it when any high-impact
        # event falls inside that hold window (event would spike IV and direction).
        if "theta_decay" in allowed and macro_calendar.is_blackout_window(today, window_days=3):
            allowed = [s for s in allowed if s != "theta_decay"]
            logger.debug("theta_decay blocked: macro event within 3-day hold window")

        # Build MarketContext for confluence scoring (includes options chain context)
        chain_iv_rank = (
            self._current_chain.iv_rank if self._current_chain is not None else 50.0
        )
        ctx = MarketContext(
            df_1min=self._df_1min if self._df_1min is not None else pd.DataFrame(),
            df_5min=self._df_5min if self._df_5min is not None else pd.DataFrame(),
            df_15min=self._df_15min if self._df_15min is not None else pd.DataFrame(),
            df_30min=self._df_30min if self._df_30min is not None else pd.DataFrame(),
            df_1hr=self._df_1hr if self._df_1hr is not None else pd.DataFrame(),
            df_4hr=self._df_4hr if self._df_4hr is not None else pd.DataFrame(),
            regime=self.current_regime,
            iv_rank=chain_iv_rank,
            vix=self._current_vix,
        )

        # Collect all signals from eligible strategies
        candidates = []
        for strat_name in allowed:
            strategy = self.strategies.get(strat_name)
            if not strategy:
                continue

            signal = None
            if strat_name == "ema_crossover" and self._df_5min is not None and len(self._df_5min) > 30:
                idx = len(self._df_5min) - 1
                signal = strategy.generate_signal(self._df_5min, idx, now, market_context=ctx)
            elif strat_name == "mtf_momentum":
                if (self._df_1min is not None and len(self._df_1min) > 30
                        and self._df_5min is not None and len(self._df_5min) > 20
                        and self._df_15min is not None and len(self._df_15min) > 10):
                    idx = len(self._df_1min) - 1
                    signal = strategy.generate_signal(
                        self._df_1min, idx, now,
                        df_5min=self._df_5min, df_15min=self._df_15min,
                        market_context=ctx,
                    )
            elif strat_name == "trend_continuation":
                # Needs 5-min bars (accessed via market_context.df_5min)
                if (self._df_1min is not None and len(self._df_1min) > 30
                        and self._df_5min is not None and len(self._df_5min) > 25):
                    idx = len(self._df_1min) - 1
                    signal = strategy.generate_signal(self._df_1min, idx, now, market_context=ctx)
            elif strat_name == "smc_ict" and self._df_1min is not None and len(self._df_1min) > 60:
                idx = len(self._df_1min) - 1
                signal = strategy.generate_signal(
                    self._df_1min, idx, now,
                    market_context=ctx,
                    df_qqq_5min=self._df_qqq_5min,
                    df_qqq_15min=self._df_qqq_15min,
                )
            elif self._df_1min is not None and len(self._df_1min) > 30:
                idx = len(self._df_1min) - 1
                signal = strategy.generate_signal(self._df_1min, idx, now, market_context=ctx)

            if signal:
                # smc_ict has its own A+/A/B confluence rating — don't overwrite it
                if strat_name != "smc_ict":
                    confluence = BaseStrategy.compute_confluence_score(ctx, signal.direction)
                    confluence_weight = confluence / 100.0
                    signal.confidence = signal.confidence * 0.6 + confluence_weight * 0.4

                score = signal.confidence
                candidates.append((strat_name, signal, score))

        # Hard regime-direction filter: trending regimes accept only the regime-aligned direction.
        # This prevents e.g. LONG signals firing in TRENDING_DOWN (cause of live losses).
        if self.current_regime == MarketRegime.TRENDING_UP:
            pre = len(candidates)
            candidates = [(s, sig, sc) for s, sig, sc in candidates if sig.direction == Direction.LONG]
            if len(candidates) < pre:
                logger.debug(f"Regime filter: dropped {pre - len(candidates)} SHORT signal(s) in TRENDING_UP")
        elif self.current_regime == MarketRegime.TRENDING_DOWN:
            pre = len(candidates)
            candidates = [(s, sig, sc) for s, sig, sc in candidates if sig.direction == Direction.SHORT]
            if len(candidates) < pre:
                logger.debug(f"Regime filter: dropped {pre - len(candidates)} LONG signal(s) in TRENDING_DOWN")

        # News MEDIUM risk: apply 8% confidence penalty to all candidates.
        # Does not block outright but raises the effective bar for entry.
        if news_risk == "MEDIUM" and candidates:
            candidates = [(s, sig, sc * 0.92) for s, sig, sc in candidates]
            logger.debug("News risk=MEDIUM — applied 8% confidence penalty to all candidates")

        # Execute best-scored signal (filtered by minimum confidence)
        if candidates:
            min_conf = settings.min_signal_confidence
            candidates = [(s, sig, sc) for s, sig, sc in candidates if sig.confidence >= min_conf]
        if not candidates:
            return

        # Weight by blended backtest + live performance score from strategy_monitor
        if self._strategy_scores:
            weighted = []
            for strat_name, signal, score in candidates:
                backtest_score = self._strategy_scores.get(strat_name)  # None if no backtest data
                if backtest_score is None:
                    # No backtest data yet — use a modest default performance weight so
                    # backtest-validated technical strategies naturally outcompete this
                    # strategy when their specific conditions fire.
                    # perf_weight=0.25 ≈ composite score of 10 (just above the gate)
                    default_perf_weight = 0.25
                    blended = score * 0.6 + default_perf_weight * 0.4
                    weighted.append((strat_name, signal, blended))
                    continue
                # Hard gate: skip strategies with clearly negative expected value
                blended_perf = strategy_monitor.get_blended_score(strat_name, backtest_score)
                if blended_perf < MIN_COMPOSITE_SCORE_TO_TRADE:
                    logger.debug(
                        f"Skipping {strat_name}: blended score {blended_perf:.1f} "
                        f"< {MIN_COMPOSITE_SCORE_TO_TRADE} minimum"
                    )
                    continue
                # Normalize blended_perf (range −20..100) to 0-1 weight
                perf_weight = max(0.0, min((blended_perf + 20) / 120.0, 1.0))
                # Final score: 60% signal quality + 40% blended performance
                blended = score * 0.6 + perf_weight * 0.4
                weighted.append((strat_name, signal, blended))
            candidates = weighted

        if not candidates:
            return

        # Multi-strategy agreement bonus: if 2+ candidates agree on direction,
        # boost the top candidate's score by 15% as signals are confirming each other
        if len(candidates) >= 2:
            directions = [sig.direction for _, sig, _ in candidates]
            long_count  = directions.count("LONG")
            short_count = directions.count("SHORT")
            agreement = max(long_count, short_count)
            if agreement >= 2:
                # Sort first so we know which is the best candidate
                candidates.sort(key=lambda x: x[2], reverse=True)
                top_strat, top_sig, top_score = candidates[0]
                agreement_bonus = 0.15 * (agreement - 1) / max(len(candidates) - 1, 1)
                top_score = min(1.0, top_score * (1.0 + agreement_bonus))
                candidates[0] = (top_strat, top_sig, top_score)
                logger.debug(
                    f"Agreement bonus: {agreement}/{len(candidates)} strategies agree "
                    f"({top_sig.direction.value}) → +{agreement_bonus:.1%} for {top_strat}"
                )

        candidates.sort(key=lambda x: x[2], reverse=True)
        strat_name, signal, score = candidates[0]

        regime_str = self.current_regime.value

        # ── Trade memory: compare to similar historical setups ────────────────
        # Build a lightweight context dict from what we know pre-order-selection.
        # entry_iv: normalize IV rank (0-100) to 0-1; entry_theta is approximate.
        iv_approx = (self._current_chain.iv_rank / 100.0) if self._current_chain else 0.20
        memory_context = {
            "entry_time": now.isoformat(),
            "confidence": signal.confidence,
            "entry_delta": signal.metadata.get("target_delta", 0.20),
            "entry_iv": iv_approx,
            "entry_theta": 0.01,   # estimated; precise value comes after selector
            "max_loss": 300.0,     # conservative placeholder
            "option_strategy_type": (
                "PUT_CREDIT_SPREAD" if signal.direction.value == "LONG"
                else "CALL_CREDIT_SPREAD"
            ),
            "regime": regime_str,
        }
        memory_result = query_similar_trades(
            closed_trades=self.paper_engine.closed_trades,
            context=memory_context,
        )
        if memory_result["verdict"] == "BLOCK":
            logger.info(
                f"TradeMemory: blocking {strat_name} — "
                f"similar setups WR={memory_result.get('win_rate', '?'):.0%} "
                f"(n={memory_result['similar_count']})"
            )
            return

        # Map signal to options order via selector
        risk_fraction = self.risk_manager._kelly_risk_fraction()   # already VIX-adjusted
        risk_fraction *= max(0.3, min(signal.confidence, 1.0))
        risk_fraction *= self.risk_manager._time_of_day_scalar()

        # Trade memory penalty: reduce size when similar past setups underperformed
        if memory_result["verdict"] == "PENALISE":
            risk_fraction *= memory_result["confidence_multiplier"]
            logger.info(
                f"TradeMemory: {strat_name} size ×{memory_result['confidence_multiplier']:.0%} "
                f"— similar WR={memory_result.get('win_rate', '?'):.0%}"
            )

        # Additional VIX stress penalty: further reduce size in backwardation (near-term fear)
        if vix_backwardation:
            risk_fraction *= 0.6
            logger.debug(
                f"VIX backwardation ({self._vix_term_ratio:.2f}) — applying 0.6x size penalty"
            )

        # ── Adversarial consultant: pressure-test before execution ────────────
        upcoming_events = macro_calendar.upcoming_events(days_ahead=7)
        portfolio_delta = self.paper_engine.portfolio_net_delta
        if portfolio_delta != 0.0:
            logger.debug(f"Portfolio net delta: {portfolio_delta:+.3f}")

        advisor_result = await advisor_assess(
            signal=signal,
            regime_str=regime_str,
            vix=self._current_vix,
            news_risk=news_risk,
            upcoming_events=upcoming_events,
            daily_pnl=self.paper_engine.daily_pnl,
            portfolio_delta=portfolio_delta,
            memory_result=memory_result,
        )
        advisor_verdict = advisor_result.get("verdict", "PROCEED")
        if advisor_verdict == "BLOCK":
            logger.info(
                f"TradeAdvisor: BLOCKED {strat_name} | "
                f"{'; '.join(advisor_result.get('risk_factors', [])[:2])}"
            )
            return
        elif advisor_verdict == "REDUCE":
            risk_fraction *= 0.5
            logger.info(
                f"TradeAdvisor: REDUCE {strat_name} — halving size | "
                f"{'; '.join(advisor_result.get('risk_factors', [])[:2])}"
            )

        order = self.options_selector.select(
            signal, self.current_regime, self._current_chain,
            self.paper_engine.capital, risk_fraction,
        )
        if order is None:
            logger.debug(f"Options selector returned None for {strat_name}")
            return

        # Portfolio risk check
        open_risk = self.paper_engine.open_risk
        contracts = options_sizing.calculate_contracts(
            order, self.paper_engine.capital, risk_fraction, open_risk,
        )
        if contracts <= 0:
            return

        # Update contract count on the order (scale max_loss/max_profit proportionally)
        old_contracts = max(1, order.contracts)
        for leg in order.legs:
            leg.quantity = contracts
        order.contracts = contracts
        order.max_loss = (order.max_loss / old_contracts) * contracts
        order.max_profit = (order.max_profit / old_contracts) * contracts
        order.regime = self.current_regime.value

        # Reject trades where commission would exceed max profit
        total_legs = sum(leg.quantity for leg in order.legs)
        estimated_commission = total_legs * settings.options_commission_per_contract * 2
        total_premium = abs(order.net_premium) * contracts * 100
        if estimated_commission >= total_premium * 0.5:
            logger.warning(
                f"Rejecting {strat_name}: commission ${estimated_commission:.2f} "
                f">= 50% of premium ${total_premium:.2f}"
            )
            return

        # Reject credit spreads with unreasonably low premium (<$0.10 per contract)
        if order.is_credit and abs(order.net_premium) < 0.10:
            logger.warning(
                f"Rejecting {strat_name}: credit premium ${abs(order.net_premium):.4f} "
                f"too low (min $0.10)"
            )
            return

        if self.mode == "paper":
            pos = self.paper_engine.open_position(order)
            if pos:
                abbrev = STRATEGY_ABBREV.get(order.strategy_type, order.strategy_type.value)
                await ws_manager.broadcast("trade_update", {
                    "action": "OPEN",
                    "strategy": strat_name,
                    "direction": "LONG" if not order.is_credit else "SHORT",
                    "option_strategy_type": order.strategy_type.value,
                    "contracts": contracts,
                    "net_premium": round(order.net_premium, 4),
                    "max_loss": round(order.max_loss, 2),
                    "max_profit": round(order.max_profit, 2),
                    "legs": order.legs_to_json(),
                    "regime": self.current_regime.value,
                    "display": order.to_display_string(),
                    "confidence": round(order.confidence, 4) if order.confidence else None,
                    "strike": order.primary_strike,
                    "expiration_date": order.primary_expiration,
                    "option_type": order.primary_option_type,
                    "entry_delta": round(order.net_delta, 4),
                    "entry_iv": round(order.legs[0].iv, 4) if order.legs else None,
                })
        else:
            # Live mode — place options order via Schwab
            from app.services.schwab_client import schwab_client
            result = await schwab_client.place_options_order(order)
            if result and result.get("status") == "FILLED":
                pos = self.paper_engine.open_position(order)
                if pos:
                    await ws_manager.broadcast("trade_update", {
                        "action": "OPEN",
                        "strategy": strat_name,
                        "option_strategy_type": order.strategy_type.value,
                        "contracts": contracts,
                        "live": True,
                        "display": order.to_display_string(),
                    })

    async def _check_exits(self):
        """Check options-specific exit rules."""
        pos = self.paper_engine.position
        if not pos:
            return

        now = datetime.now(ET)
        current_price = self._get_last_price()

        if current_price <= 0:
            return

        # Update position with current underlying price
        pos.update(current_price)

        # 1. Expiration day close at 3:50 PM
        if pos.order.primary_expiration:
            try:
                exp_date = datetime.strptime(pos.order.primary_expiration, "%Y-%m-%d").date()
                if now.date() == exp_date and now.time() >= time(15, 50):
                    await self._close_options_position(current_price, "expiration_day_close")
                    return
            except ValueError:
                pass

        # 2. EOD exit at 3:55 PM
        if now.time() >= time(15, 55):
            await self._close_options_position(current_price, "eod")
            return

        # 3. Strategy-specific profit/loss targets with time-based trailing stops
        exit_rules = OPTIONS_EXIT_RULES.get(pos.strategy_type, {})
        take_profit_pct = exit_rules.get("take_profit_pct", 0.50)
        initial_stop = exit_rules.get("initial_stop_mult", 2.0)
        tight_stop = exit_rules.get("tight_stop_mult", 1.0)
        dte_tighten = exit_rules.get("dte_tighten", 3)

        # Calculate current stop multiplier based on DTE
        # Linearly interpolate from initial_stop to tight_stop as DTE decreases
        dte = 99
        if pos.order.primary_expiration:
            try:
                exp_date = datetime.strptime(pos.order.primary_expiration, "%Y-%m-%d").date()
                dte = (exp_date - now.date()).days
            except ValueError:
                pass

        if dte <= dte_tighten:
            stop_mult = tight_stop
        elif dte >= dte_tighten + 5:
            stop_mult = initial_stop
        else:
            # Linear interpolation
            t = (dte - dte_tighten) / 5.0
            stop_mult = tight_stop + t * (initial_stop - tight_stop)

        # Use raw P&L (no commission) for stop/profit checks
        # Commission should not trigger stop losses
        raw_pnl = pos.raw_pnl()
        pnl = pos.unrealized_pnl()  # net P&L for display/logging
        max_profit = pos.order.max_profit

        if pos.is_credit:
            # Credit spread: close at X% of max profit
            profit_pct_of_max = raw_pnl / max_profit if max_profit > 0 else 0
            if profit_pct_of_max >= take_profit_pct:
                await self._close_options_position(
                    current_price,
                    f"take_profit_{take_profit_pct:.0%}_max",
                )
                return

            # Credit spread stop: loss exceeds stop_mult x credit received
            entry_credit = pos.entry_net_premium * pos.order.contracts * 100
            if raw_pnl < 0 and abs(raw_pnl) >= entry_credit * stop_mult:
                await self._close_options_position(
                    current_price,
                    f"stop_loss_{stop_mult:.1f}x_credit",
                )
                return
        else:
            # Debit spread / long option: close at X% gain of premium
            entry_cost = pos.entry_net_premium * pos.order.contracts * 100
            if entry_cost > 0:
                gain_pct = raw_pnl / entry_cost
                if gain_pct >= take_profit_pct:
                    await self._close_options_position(
                        current_price,
                        f"take_profit_{take_profit_pct:.0%}_premium",
                    )
                    return

                if gain_pct <= -stop_mult:
                    await self._close_options_position(
                        current_price,
                        f"stop_loss_{stop_mult:.0%}_premium",
                    )
                    return

        # 4. Trailing stop — protect profits once position gains enough
        ts_trigger = settings.trailing_stop_trigger_pct   # e.g. 0.25
        ts_trail   = settings.trailing_stop_trail_pct     # e.g. 0.20
        entry_prem = pos.entry_net_premium

        if pos.is_credit:
            # Credit: best_premium = lowest cost-to-close seen (= most profit).
            # Activate when we've gained > trigger_pct of the credit received.
            profit_captured = (entry_prem - pos.best_premium) / entry_prem if entry_prem > 0 else 0
            if profit_captured >= ts_trigger:
                # Trail: stop if current cost-to-close rises > trail_pct above best
                trail_level = pos.best_premium * (1.0 + ts_trail)
                if pos.current_premium >= trail_level:
                    await self._close_options_position(
                        current_price,
                        f"trailing_stop_{ts_trail:.0%}_from_best",
                    )
                    return
        else:
            # Debit: best_premium = highest value seen since entry.
            # Activate when best has exceeded entry by > trigger_pct.
            gain_from_best = (pos.best_premium - entry_prem) / entry_prem if entry_prem > 0 else 0
            if gain_from_best >= ts_trigger:
                # Trail: stop if current premium falls > trail_pct below the best
                trail_level = pos.best_premium * (1.0 - ts_trail)
                if pos.current_premium <= trail_level:
                    await self._close_options_position(
                        current_price,
                        f"trailing_stop_{ts_trail:.0%}_from_best",
                    )
                    return

        # 5. Delta floor exit — long options only (hedge fund: exit dying options early)
        # When the net delta of a long position falls below 0.20, the option is far OTM.
        # The rate of further premium decay accelerates sharply; holding is value destruction.
        if not pos.is_credit and pos.strategy_type not in (
            OptionsStrategyType.LONG_STRADDLE, OptionsStrategyType.LONG_STRANGLE,
        ):
            net_delta_per_contract = 0.0
            for leg in pos.order.legs:
                sign = -1.0 if "SELL" in leg.action.value else 1.0
                net_delta_per_contract += sign * abs(leg.delta)
            net_delta_per_contract = abs(net_delta_per_contract)
            if 0 < net_delta_per_contract < 0.20:
                await self._close_options_position(
                    current_price, f"delta_floor_exit_{net_delta_per_contract:.2f}"
                )
                return

        # 6. Theta time-stop — long options approaching expiration (hedge fund: never bleed theta)
        # After 15:30 ET with DTE ≤ 1, theta decay is catastrophic for long options.
        if not pos.is_credit and now.time() >= time(15, 30) and dte <= 1:
            await self._close_options_position(current_price, "theta_time_stop_eod")
            return

        # 7. Straddle/strangle: check total position P&L
        if pos.strategy_type in (
            OptionsStrategyType.LONG_STRADDLE,
            OptionsStrategyType.LONG_STRANGLE,
        ):
            entry_cost = pos.entry_net_premium * pos.order.contracts * 100
            if entry_cost > 0:
                total_gain = raw_pnl / entry_cost
                if total_gain <= -stop_mult:
                    await self._close_options_position(
                        current_price,
                        f"straddle_stop_{stop_mult:.0%}_total",
                    )
                    return

    async def _close_options_position(self, underlying_price: float, reason: str):
        """Close the current options position and update all performance trackers."""
        if self.mode == "live":
            from app.services.schwab_client import schwab_client
            if schwab_client.is_configured and self.paper_engine.position:
                await schwab_client.close_options_position(
                    self.paper_engine.position.order,
                )

        trade = self.paper_engine.close_position(underlying_price, reason)
        if trade:
            strat_name = trade.get("strategy", "")
            pnl = trade.get("pnl", 0.0)

            await self._persist_trade(trade)
            self.risk_manager.record_trade_result(pnl)

            # Update per-strategy live performance and persist to DB
            if strat_name:
                strategy_monitor.record_trade(strat_name, pnl)
                should_disable, disable_reason = strategy_monitor.should_auto_disable(strat_name)
                if should_disable:
                    strategy_monitor.mark_disabled(strat_name, disable_reason)
                    self.enabled_strategies.discard(strat_name)
                    logger.warning(f"Auto-disabled strategy [{strat_name}]: {disable_reason}")
                    await ws_manager.broadcast("status_update", {
                        "strategy_auto_disabled": strat_name,
                        "reason": disable_reason,
                    })
                # Fire-and-forget DB save
                try:
                    from app.database import async_session
                    async with async_session() as db:
                        await strategy_monitor.save_to_db(strat_name, db)
                except Exception as e:
                    logger.warning(f"Could not persist strategy monitor stats: {e}")

            await ws_manager.broadcast("trade_update", {
                "action": "CLOSE",
                **trade,
            })

    async def _persist_trade(self, trade_dict: dict):
        """Persist a closed trade to the database."""
        try:
            from app.database import async_session
            from app.models import Trade as TradeModel
            async with async_session() as db:
                db_trade = TradeModel(
                    symbol=trade_dict.get("symbol", "SPY"),
                    direction=trade_dict["direction"],
                    strategy=trade_dict["strategy"],
                    regime=self.current_regime.value if self.current_regime else None,
                    quantity=trade_dict["quantity"],
                    entry_price=trade_dict["entry_price"],
                    entry_time=datetime.fromisoformat(trade_dict["entry_time"]),
                    exit_price=trade_dict.get("exit_price"),
                    exit_time=datetime.fromisoformat(trade_dict["exit_time"]) if trade_dict.get("exit_time") else None,
                    stop_loss=trade_dict.get("stop_loss"),
                    take_profit=trade_dict.get("take_profit"),
                    pnl=trade_dict.get("pnl"),
                    pnl_pct=trade_dict.get("pnl_pct"),
                    exit_reason=trade_dict.get("exit_reason"),
                    is_paper=(self.mode == "paper"),
                    status="CLOSED",
                    confidence=trade_dict.get("confidence"),
                    slippage=trade_dict.get("slippage"),
                    commission=trade_dict.get("commission"),
                    mae=trade_dict.get("mae"),
                    mfe=trade_dict.get("mfe"),
                    mae_pct=trade_dict.get("mae_pct"),
                    mfe_pct=trade_dict.get("mfe_pct"),
                    bars_held=trade_dict.get("bars_held"),
                    # Options fields
                    option_strategy_type=trade_dict.get("option_strategy_type"),
                    contract_symbol=trade_dict.get("contract_symbol"),
                    legs_json=trade_dict.get("legs_json"),
                    strike=trade_dict.get("strike"),
                    expiration_date=trade_dict.get("expiration_date"),
                    option_type=trade_dict.get("option_type"),
                    net_premium=trade_dict.get("net_premium"),
                    max_loss=trade_dict.get("max_loss"),
                    max_profit=trade_dict.get("max_profit"),
                    entry_delta=trade_dict.get("entry_delta"),
                    entry_theta=trade_dict.get("entry_theta"),
                    entry_iv=trade_dict.get("entry_iv"),
                    underlying_entry=trade_dict.get("underlying_entry"),
                    underlying_exit=trade_dict.get("underlying_exit"),
                    contracts=trade_dict.get("contracts"),
                )
                db.add(db_trade)
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist trade: {e}")

    def _get_last_price(self) -> float:
        """Return last known market price."""
        if self._df_1min is not None and not self._df_1min.empty:
            return float(self._df_1min.iloc[-1]["close"])
        if self.paper_engine.position:
            return self.paper_engine.position.entry_underlying
        return 0.0

    def get_status(self) -> dict:
        pos = self.paper_engine.position
        open_pos = None
        if pos:
            current_price = self._get_last_price() or pos.entry_underlying
            pos.update(current_price)
            order = pos.order
            abbrev = STRATEGY_ABBREV.get(order.strategy_type, order.strategy_type.value)
            open_pos = {
                "symbol": "SPY",
                "direction": "LONG" if not order.is_credit else "SHORT",
                "quantity": order.contracts,
                "entry_price": round(pos.entry_net_premium, 4),
                "entry_time": pos.entry_time.isoformat(),
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "strategy": order.signal_strategy,
                "unrealized_pnl": round(pos.unrealized_pnl(), 2),
                # Options fields
                "option_strategy_type": order.strategy_type.value,
                "option_strategy_abbrev": abbrev,
                "contracts": order.contracts,
                "net_premium": round(order.net_premium, 4),
                "max_loss": round(order.max_loss, 2),
                "max_profit": round(order.max_profit, 2),
                "net_delta": round(order.net_delta, 4),
                "net_theta": round(order.net_theta, 4),
                "legs": order.legs_to_json(),
                "underlying_price": round(current_price, 2),
                "expiration_date": order.primary_expiration,
                "display": order.to_display_string(),
            }
        # Determine VIX macro regime label for UI
        if self._current_vix >= 35:
            vix_regime = "EXTREME_FEAR"
        elif self._current_vix >= 25:
            vix_regime = "STRESSED"
        elif self._current_vix >= 18:
            vix_regime = "ELEVATED"
        else:
            vix_regime = "CALM"

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
            "equity": round(self.paper_engine.total_equity(self._get_last_price()), 2),
            "peak_equity": round(self.paper_engine.peak_equity, 2),
            "drawdown_pct": round(self.paper_engine.drawdown_pct * 100, 2),
            "total_pnl": round(self.paper_engine.capital - self.paper_engine.initial_capital, 2),
            # VIX macro regime (hedge fund gate)
            "vix": round(self._current_vix, 1),
            "vix_term_ratio": round(self._vix_term_ratio, 3),
            "vix_regime": vix_regime,
        }


# Singleton
trading_engine = TradingEngine()
