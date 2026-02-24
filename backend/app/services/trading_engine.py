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
from app.services.strategies.adx_trend import ADXTrendStrategy
from app.services.strategies.golden_cross import GoldenCrossStrategy
from app.services.strategies.keltner_breakout import KeltnerBreakoutStrategy
from app.services.strategies.williams_r import WilliamsRStrategy
from app.services.strategies.rsi2_mean_reversion import RSI2MeanReversionStrategy
from app.services.strategies.stoch_rsi import StochRSIStrategy
from app.services.strategies.smc_supply_demand import SMCSupplyDemandStrategy
from app.services.strategies.mtf_ma_sr import MtfMaSRStrategy
from app.services.strategies.smart_rsi import SmartRSIStrategy
from app.services.strategy_monitor import strategy_monitor
from app.websocket import ws_manager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: [
        "orb", "ema_crossover", "mtf_momentum", "micro_pullback", "momentum_scalper",
        "adx_trend", "golden_cross", "mtf_ma_sr", "rsi2_mean_reversion",
    ],
    MarketRegime.TRENDING_DOWN: [
        "orb", "ema_crossover", "mtf_momentum", "micro_pullback", "momentum_scalper",
        "adx_trend", "golden_cross", "mtf_ma_sr", "rsi2_mean_reversion",
    ],
    MarketRegime.RANGE_BOUND: [
        "vwap_reversion", "volume_flow", "rsi_divergence", "bb_squeeze",
        "double_bottom_top", "williams_r", "stoch_rsi", "smart_rsi",
        "smc_supply_demand",
    ],
    MarketRegime.VOLATILE: [
        "vwap_reversion", "volume_flow", "macd_reversal", "gap_fill",
        "keltner_breakout", "williams_r", "smart_rsi", "smc_supply_demand",
        "stoch_rsi",
    ],
}

# Minimum blended composite score to allow a strategy to trade
# (prevents strategies with clearly negative expectancy from taking positions)
MIN_COMPOSITE_SCORE_TO_TRADE = -5.0


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
            # Original 12
            "vwap_reversion":      VWAPReversionStrategy(),
            "orb":                 ORBStrategy(),
            "ema_crossover":       EMACrossoverStrategy(),
            "volume_flow":         VolumeFlowStrategy(),
            "mtf_momentum":        MTFMomentumStrategy(),
            "rsi_divergence":      RSIDivergenceStrategy(),
            "bb_squeeze":          BBSqueezeStrategy(),
            "macd_reversal":       MACDReversalStrategy(),
            "momentum_scalper":    MomentumScalperStrategy(),
            "gap_fill":            GapFillStrategy(),
            "micro_pullback":      MicroPullbackStrategy(),
            "double_bottom_top":   DoubleBottomTopStrategy(),
            # Technical additions
            "adx_trend":           ADXTrendStrategy(),
            "golden_cross":        GoldenCrossStrategy(),
            "keltner_breakout":    KeltnerBreakoutStrategy(),
            "williams_r":          WilliamsRStrategy(),
            # TradingView screenshot strategies
            "rsi2_mean_reversion": RSI2MeanReversionStrategy(),
            "stoch_rsi":           StochRSIStrategy(),
            "smc_supply_demand":   SMCSupplyDemandStrategy(),
            "mtf_ma_sr":           MtfMaSRStrategy(),
            "smart_rsi":           SmartRSIStrategy(),
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

    async def start(self):
        if self.running:
            return
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

        if self._current_chain is None:
            logger.debug("No option chain available, skipping entry check")
            return

        # Filter strategies by regime AND auto-disable status
        allowed = [
            s for s in self.enabled_strategies
            if s in REGIME_STRATEGY_MAP.get(self.current_regime, [])
            and not strategy_monitor.is_auto_disabled(s)
        ]

        # Build MarketContext for confluence scoring
        ctx = MarketContext(
            df_1min=self._df_1min if self._df_1min is not None else pd.DataFrame(),
            df_5min=self._df_5min if self._df_5min is not None else pd.DataFrame(),
            df_15min=self._df_15min if self._df_15min is not None else pd.DataFrame(),
            df_30min=self._df_30min if self._df_30min is not None else pd.DataFrame(),
            df_1hr=self._df_1hr if self._df_1hr is not None else pd.DataFrame(),
            df_4hr=self._df_4hr if self._df_4hr is not None else pd.DataFrame(),
            regime=self.current_regime,
        )

        # Collect all signals from eligible strategies
        candidates = []
        now = datetime.now(ET)
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
            elif self._df_1min is not None and len(self._df_1min) > 30:
                idx = len(self._df_1min) - 1
                signal = strategy.generate_signal(self._df_1min, idx, now, market_context=ctx)

            if signal:
                # Apply multi-timeframe confluence scoring
                confluence = BaseStrategy.compute_confluence_score(ctx, signal.direction)
                confluence_weight = confluence / 100.0
                signal.confidence = signal.confidence * 0.6 + confluence_weight * 0.4

                score = signal.confidence
                candidates.append((strat_name, signal, score))

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
                backtest_score = self._strategy_scores.get(strat_name, 0.0)
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

        # Map signal to options order via selector
        risk_fraction = self.risk_manager._kelly_risk_fraction()
        risk_fraction *= max(0.3, min(signal.confidence, 1.0))
        risk_fraction *= self.risk_manager._time_of_day_scalar()

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

        # 4. Straddle/strangle: check total position P&L
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
        }


# Singleton
trading_engine = TradingEngine()
