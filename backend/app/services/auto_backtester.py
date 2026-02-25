"""Automated background backtester — runs on startup and every 4 hours.

Tests each strategy individually + curated combinations across 3 date ranges.
Also runs a 15-year long-term daily-bar backtest per strategy on demand.

Composite scoring:
  - Short-term only:  composite_score = st_composite_score
  - After LT run:     composite_score = 0.55 * st_composite + 0.45 * lt_composite

LT weight is higher (45%) because 15 years of daily data is far more statistically
significant than 30 days of 1-min bars.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd

from app.services.backtester import Backtester, STRATEGY_MAP

logger = logging.getLogger(__name__)

from app.services.long_term_backtester import (
    ALL_STRATEGIES as LT_ALL_STRATEGIES,
    LT_ONLY_STRATEGIES,
)

ALL_STRATEGIES = list(STRATEGY_MAP.keys())

# Long-term retraining interval (seconds) — default 7 days
LT_RETRAIN_INTERVAL_HOURS = 7 * 24

# Curated multi-strategy combinations
COMBO_CONFIGS = [
    ["vwap_reversion", "orb", "ema_crossover"],
    ["vwap_reversion", "volume_flow", "rsi_divergence"],
    ["orb", "ema_crossover", "mtf_momentum", "micro_pullback"],
    ["bb_squeeze", "rsi_divergence", "double_bottom_top"],
    ["macd_reversal", "gap_fill", "vwap_reversion"],
    ["momentum_scalper", "micro_pullback", "ema_crossover"],
    ["vwap_reversion", "orb", "ema_crossover", "volume_flow", "mtf_momentum"],
    ALL_STRATEGIES[:6],
    ALL_STRATEGIES,
]

# Default long-term window: ~15 years back from today
LT_START_DATE = "2010-01-01"


def _date_ranges() -> list[dict]:
    """Return 3 date range configs: 1-day, 5-day, 30-day."""
    today = datetime.now(timezone.utc).date()
    return [
        {
            "label": "1d",
            "start": (today - timedelta(days=1)).isoformat(),
            "end": today.isoformat(),
            "interval": "1m",
        },
        {
            "label": "5d",
            "start": (today - timedelta(days=7)).isoformat(),
            "end": today.isoformat(),
            "interval": "1m",
        },
        {
            "label": "30d",
            "start": (today - timedelta(days=35)).isoformat(),
            "end": today.isoformat(),
            "interval": "5m",
        },
    ]


def _compute_lt_composite(result) -> float:
    """
    Compute long-term composite score on the same -20..100 scale as short-term.

    Requires at least MIN_TRADES_FOR_LT_SCORE trades; returns 0 otherwise
    to avoid rewarding strategies that almost never fire.

    Normalisation anchors (→ ~100 pts at excellent, ~0 at breakeven, <0 at poor):
      PF:      1.0=0, 1.5=25, 2.0=50, 0.5=-25   (primary edge quality metric)
      Sharpe:  0=0, 1=100, -1=-100               (0% rf, annualised)
      CAGR:    0%=0, 1%=100, -0.5%=-50           (calibrated for day-trading on SPY)
      Sortino: 0=0, 1.5=100
      WinRate: 50%=50, 100%=100
      MaxDD:   0%=100, 20%=0, 40%=-100           (lower is better)
      Yearly consistency: % of profitable years scaled to ±15 pts bonus/penalty
    """
    MIN_TRADES_FOR_LT_SCORE = 10
    if result.total_trades < MIN_TRADES_FOR_LT_SCORE:
        logger.warning(
            f"LT composite skipped: only {result.total_trades} trades "
            f"(need ≥{MIN_TRADES_FOR_LT_SCORE}); returning neutral score of 0"
        )
        return 0.0

    pf_score     = min(max((result.profit_factor - 1.0) / 1.0, -1.0), 1.0) * 50
    sharpe_score = min(max(result.sharpe_ratio / 1.0,   -1.0), 1.0) * 100
    cagr_score   = min(max(result.cagr_pct     / 1.0,   -1.0), 1.0) * 100
    sortino_score= min(max(result.sortino_ratio / 1.5,  -0.5), 1.0) * 100
    wr_score     = result.win_rate * 100
    dd_score     = max(-100.0, 100.0 - result.max_drawdown_pct * 5.0)

    # Yearly consistency bonus: strategies that profit in ≥70% of years are
    # more reliable than those with sporadic big wins in a few years.
    yearly_bonus = 0.0
    if result.yearly_returns:
        n_years      = len(result.yearly_returns)
        n_profitable = sum(1 for y in result.yearly_returns if y.get("return_pct", 0) > 0)
        profitable_pct = n_profitable / n_years if n_years > 0 else 0.5
        # Scale: 70%+ profitable years → +15, 50% → 0, <30% → -15
        yearly_bonus = (profitable_pct - 0.5) * 30.0   # range: -15 to +15

    return round(
        0.25 * sharpe_score
        + 0.20 * cagr_score
        + 0.20 * pf_score
        + 0.15 * sortino_score
        + 0.10 * wr_score
        + 0.10 * dd_score
        + yearly_bonus,
        2,
    )


def _blend(st: float, lt: float | None) -> float:
    """55% short-term + 45% long-term when LT data is available."""
    if lt is None:
        return round(st, 2)
    return round(0.55 * st + 0.45 * lt, 2)


class AutoBacktester:
    """Singleton background backtester."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._progress: dict = {
            "status": "idle",
            "current_test": "",
            "completed": 0,
            "total": 0,
            "errors": 0,
            "last_run": None,
            "results": [],
        }
        self._lt_progress: dict = {
            "status": "idle",
            "current_test": "",
            "completed": 0,
            "total": 0,
            "errors": 0,
            "last_run": None,
            "start_date": LT_START_DATE,
            "end_date": "",
        }
        self._lt_task: Optional[asyncio.Task] = None

    @property
    def progress(self) -> dict:
        return dict(self._progress)

    @property
    def lt_progress(self) -> dict:
        return dict(self._lt_progress)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("AutoBacktester started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._lt_task:
            self._lt_task.cancel()
            try:
                await self._lt_task
            except asyncio.CancelledError:
                pass
            self._lt_task = None
        logger.info("AutoBacktester stopped")

    async def trigger(self):
        """Manually trigger a short-term backtest run."""
        if self._progress["status"] == "running":
            return
        asyncio.create_task(self._run_backtests())

    async def trigger_longterm(self, start_date: str = LT_START_DATE, end_date: str = ""):
        """Manually trigger long-term (daily) backtest for all 12 strategies."""
        if self._lt_progress["status"] == "running":
            logger.info("Long-term backtest already running, skipping")
            return
        if not end_date:
            end_date = datetime.now(timezone.utc).date().isoformat()
        if self._lt_task and not self._lt_task.done():
            self._lt_task.cancel()
        self._lt_task = asyncio.create_task(
            self._run_long_term_all_strategies(start_date, end_date)
        )

    # ── Short-term loop ───────────────────────────────────────────────────────

    async def _run_loop(self):
        """Run backtests on startup (after delay) then every 4 hours.
        Also triggers LT backtest on startup (if stale/missing) and every 7 days.
        """
        try:
            await asyncio.sleep(5)  # Let server finish starting

            # ── Startup LT check ─────────────────────────────────────────────
            lt_is_stale = await self._lt_data_is_stale()
            if lt_is_stale:
                logger.info("Auto-starting LT backtest on server startup (no/stale LT data)")
                asyncio.create_task(
                    self._run_long_term_all_strategies(LT_START_DATE, "")
                )

            _lt_hours_elapsed = 0.0

            while self._running:
                await self._run_backtests()

                # Check if LT retraining is due (every LT_RETRAIN_INTERVAL_HOURS)
                _lt_hours_elapsed += 4
                if _lt_hours_elapsed >= LT_RETRAIN_INTERVAL_HOURS:
                    if self._lt_progress.get("status") != "running":
                        logger.info("Scheduled weekly LT retraining triggered")
                        asyncio.create_task(
                            self._run_long_term_all_strategies(LT_START_DATE, "")
                        )
                    _lt_hours_elapsed = 0.0

                # Wait 4 hours
                for _ in range(4 * 60 * 2):  # check every 30s for cancellation
                    if not self._running:
                        return
                    await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    async def _lt_data_is_stale(self) -> bool:
        """Return True if no LT composite scores exist or last run > 8 days ago."""
        try:
            from app.database import async_session
            from app.models import StrategyRanking
            from sqlalchemy import select
            async with async_session() as db:
                rows = (await db.execute(select(StrategyRanking))).scalars().all()
                if not rows:
                    return True
                # Check if any row has LT data
                with_lt = [r for r in rows if r.lt_composite_score is not None]
                if not with_lt:
                    return True
                # Check age of last LT run
                latest = max(
                    (r.lt_computed_at for r in with_lt if r.lt_computed_at),
                    default=None,
                )
                if latest is None:
                    return True
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - latest).total_seconds() / 86400
                return age_days > 8
        except Exception as e:
            logger.warning(f"LT staleness check failed: {e}")
            return False  # Don't auto-trigger on error

    async def _run_backtests(self):
        """Execute all short-term backtests and compute rankings."""
        date_ranges = _date_ranges()

        tests: list[dict] = []
        for strat in ALL_STRATEGIES:
            for dr in date_ranges:
                tests.append({"strategies": [strat], "label": f"{strat} ({dr['label']})", **dr})
        for combo in COMBO_CONFIGS:
            for dr in date_ranges:
                combo_name = "+".join(combo[:3]) + ("..." if len(combo) > 3 else "")
                tests.append({"strategies": combo, "label": f"{combo_name} ({dr['label']})", **dr})

        self._progress = {
            "status": "running",
            "current_test": "",
            "completed": 0,
            "total": len(tests),
            "errors": 0,
            "last_run": None,
            "results": [],
        }

        logger.info(f"AutoBacktester: starting {len(tests)} short-term tests")
        results = []

        for i, test in enumerate(tests):
            if not self._running:
                break
            self._progress["current_test"] = test["label"]
            try:
                bt = Backtester(strategies=test["strategies"], use_regime_filter=True)
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda t=test: bt.run(
                        symbol="SPY",
                        start_date=t["start"],
                        end_date=t["end"],
                        interval=t["interval"],
                    ),
                )
                rd = result.to_dict()
                rd["strategies"] = ",".join(test["strategies"])
                rd["date_range"] = test["label"]
                rd["start_date"] = test["start"]
                rd["end_date"] = test["end"]
                rd["interval"] = test["interval"]
                results.append(rd)
                await self._save_result(test, result)
            except Exception as e:
                logger.warning(f"AutoBacktester: test '{test['label']}' failed: {e}")
                self._progress["errors"] += 1

            self._progress["completed"] = i + 1
            await asyncio.sleep(1)

        await self._compute_rankings(results)

        self._progress["status"] = "complete"
        self._progress["current_test"] = ""
        self._progress["last_run"] = datetime.now(timezone.utc).isoformat()
        self._progress["results"] = results
        logger.info(
            f"AutoBacktester: done. {self._progress['completed']}/{len(tests)} tests, "
            f"{self._progress['errors']} errors"
        )

    async def _save_result(self, test: dict, result):
        try:
            from app.database import async_session
            from app.models import BacktestRun
            async with async_session() as db:
                run = BacktestRun(
                    symbol="SPY",
                    start_date=test["start"],
                    end_date=test["end"],
                    interval=test["interval"],
                    initial_capital=result.initial_capital,
                    strategies=",".join(test["strategies"]),
                    total_return_pct=result.total_return_pct,
                    win_rate=result.win_rate,
                    total_trades=result.total_trades,
                    sharpe_ratio=result.sharpe_ratio,
                    max_drawdown_pct=result.max_drawdown_pct,
                    profit_factor=result.profit_factor,
                    avg_win=result.avg_win,
                    avg_loss=result.avg_loss,
                    equity_curve=result.equity_curve[-100:] if len(result.equity_curve) > 100 else result.equity_curve,
                    trades_json=result.trades,
                )
                db.add(run)
                await db.commit()
        except Exception as e:
            logger.warning(f"AutoBacktester: failed to save result: {e}")

    async def _compute_rankings(self, results: list[dict]):
        """Recompute ST composite scores, preserve existing LT data, blend composite_score."""
        try:
            from app.database import async_session
            from app.models import StrategyRanking
            from sqlalchemy import select, delete

            # ── 1. Load existing LT scores before wiping the table ────────────
            existing_lt: dict[str, dict] = {}
            async with async_session() as db:
                rows = (await db.execute(select(StrategyRanking))).scalars().all()
                for row in rows:
                    if row.lt_composite_score is not None:
                        existing_lt[row.strategy_name] = {
                            "lt_composite_score":  row.lt_composite_score,
                            "lt_cagr_pct":         row.lt_cagr_pct,
                            "lt_sharpe":           row.lt_sharpe,
                            "lt_sortino":          row.lt_sortino,
                            "lt_calmar":           row.lt_calmar,
                            "lt_max_drawdown_pct": row.lt_max_drawdown_pct,
                            "lt_win_rate":         row.lt_win_rate,
                            "lt_profit_factor":    row.lt_profit_factor,
                            "lt_total_trades":     row.lt_total_trades,
                            "lt_years_tested":     row.lt_years_tested,
                            "lt_computed_at":      row.lt_computed_at,
                        }

            # ── 2. Aggregate individual strategy ST results ───────────────────
            strategy_stats: dict[str, list[dict]] = {}
            for r in results:
                strats = r.get("strategies", "")
                if "," not in strats:
                    strategy_stats.setdefault(strats, []).append(r)

            # ── 3. Rebuild rankings table ─────────────────────────────────────
            async with async_session() as db:
                await db.execute(delete(StrategyRanking))

                for strat_name, runs in strategy_stats.items():
                    if not runs:
                        continue

                    total_trades = sum(r.get("total_trades", 0) for r in runs)

                    def safe_avg(key, rs=runs):
                        vals = [r[key] for r in rs if r.get(key) is not None]
                        return sum(vals) / len(vals) if vals else 0.0

                    avg_sharpe = safe_avg("sharpe_ratio")
                    avg_pf     = safe_avg("profit_factor")
                    avg_wr     = safe_avg("win_rate")
                    avg_ret    = safe_avg("total_return_pct")
                    avg_dd     = safe_avg("max_drawdown_pct")

                    # Short-term composite score (−100..100 scale)
                    # Weights rebalanced: profit_factor leads (most reliable edge metric
                    # for small intraday samples), then Sharpe, win_rate, return, max_dd.
                    sharpe_score = min(max(avg_sharpe / 3.0,        -1), 1) * 100
                    pf_score     = min(max((avg_pf - 1.0) / 2.0,   -1), 1) * 100
                    wr_score     = avg_wr * 100
                    ret_score    = min(max(avg_ret / 5.0,           -1), 1) * 100
                    dd_score     = max(0, 100 - avg_dd * 10)

                    # Consistency bonus (−10..+10): reward strategies that perform
                    # well across all 3 date ranges, penalise high variance.
                    # A strategy that's good on 1d AND 5d AND 30d is more reliable.
                    if len(runs) >= 3:
                        pf_vals = [r.get("profit_factor", 1.0) for r in runs if r.get("profit_factor") is not None]
                        pf_std  = float(pd.Series(pf_vals).std()) if len(pf_vals) > 1 else 0.0
                        # Low std in PF across ranges = consistent edge; high std = unreliable
                        consistency_bonus = max(-10.0, 10.0 - pf_std * 10.0)
                    else:
                        consistency_bonus = 0.0

                    st_composite = round(
                        0.30 * pf_score        # edge quality (PF most reliable)
                        + 0.25 * sharpe_score  # risk-adjusted return
                        + 0.20 * wr_score      # trade directional accuracy
                        + 0.15 * ret_score     # absolute return
                        + 0.10 * dd_score      # capital preservation
                        + consistency_bonus,   # cross-range stability
                        2,
                    )

                    # Retrieve persisted LT data
                    lt = existing_lt.get(strat_name)
                    lt_composite = lt["lt_composite_score"] if lt else None

                    ranking = StrategyRanking(
                        strategy_name=strat_name,
                        avg_sharpe_ratio=round(avg_sharpe, 4),
                        avg_profit_factor=round(avg_pf, 4),
                        avg_win_rate=round(avg_wr, 4),
                        avg_return_pct=round(avg_ret, 4),
                        avg_max_drawdown_pct=round(avg_dd, 4),
                        st_composite_score=st_composite,
                        composite_score=_blend(st_composite, lt_composite),
                        total_backtest_trades=total_trades,
                        backtest_count=len(runs),
                        computed_at=datetime.now(timezone.utc),
                        # Restore LT columns
                        lt_composite_score  = lt["lt_composite_score"]  if lt else None,
                        lt_cagr_pct         = lt["lt_cagr_pct"]         if lt else None,
                        lt_sharpe           = lt["lt_sharpe"]           if lt else None,
                        lt_sortino          = lt["lt_sortino"]          if lt else None,
                        lt_calmar           = lt["lt_calmar"]           if lt else None,
                        lt_max_drawdown_pct = lt["lt_max_drawdown_pct"] if lt else None,
                        lt_win_rate         = lt["lt_win_rate"]         if lt else None,
                        lt_profit_factor    = lt["lt_profit_factor"]    if lt else None,
                        lt_total_trades     = lt["lt_total_trades"]     if lt else None,
                        lt_years_tested     = lt["lt_years_tested"]     if lt else None,
                        lt_computed_at      = lt["lt_computed_at"]      if lt else None,
                    )
                    db.add(ranking)

                await db.commit()
                logger.info(
                    f"AutoBacktester: rankings computed for {len(strategy_stats)} strategies "
                    f"({len(existing_lt)} with LT data)"
                )
        except Exception as e:
            logger.error(f"AutoBacktester: failed to compute rankings: {e}")

    # ── Long-term daily backtest ──────────────────────────────────────────────

    async def _run_long_term_all_strategies(
        self, start_date: str, end_date: str
    ):
        """Run each of the 12 strategies independently through the 15Y daily backtester."""
        from app.config import settings
        from app.services.long_term_backtester import LongTermBacktester

        self._lt_progress = {
            "status": "running",
            "current_test": "",
            "completed": 0,
            "total": len(LT_ALL_STRATEGIES),
            "errors": 0,
            "last_run": None,
            "start_date": start_date,
            "end_date": end_date,
        }
        logger.info(
            f"LT backtest: {len(LT_ALL_STRATEGIES)} strategies | "
            f"{start_date} → {end_date}"
        )

        lt_results: dict = {}

        for strat in LT_ALL_STRATEGIES:
            self._lt_progress["current_test"] = f"{strat} ({start_date}→{end_date})"
            try:
                bt = LongTermBacktester(
                    strategies=[strat],
                    initial_capital=25_000.0,
                    max_risk_per_trade=0.015,
                    cache_dir=settings.data_cache_dir,
                )
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda s=strat: LongTermBacktester(
                        strategies=[s],
                        initial_capital=25_000.0,
                        max_risk_per_trade=0.015,
                        cache_dir=settings.data_cache_dir,
                    ).run(
                        symbol="SPY",
                        start_date=start_date,
                        end_date=end_date,
                        use_cache=True,
                    ),
                )
                lt_results[strat] = result
                logger.info(
                    f"LT [{strat}]: CAGR={result.cagr_pct:.1f}% "
                    f"Sharpe={result.sharpe_ratio:.2f} "
                    f"Sortino={result.sortino_ratio:.2f} "
                    f"WR={result.win_rate:.0%} "
                    f"Trades={result.total_trades}"
                )
            except Exception as e:
                logger.warning(f"LT backtest [{strat}] failed: {e}")
                self._lt_progress["errors"] += 1

            self._lt_progress["completed"] += 1

        # Persist LT metrics and re-blend composite scores
        await self._update_lt_rankings(lt_results)

        self._lt_progress["status"] = "complete"
        self._lt_progress["current_test"] = ""
        self._lt_progress["last_run"] = datetime.now(timezone.utc).isoformat()
        logger.info(
            f"LT backtest complete: {self._lt_progress['completed']}/{len(LT_ALL_STRATEGIES)}, "
            f"{self._lt_progress['errors']} errors"
        )

    async def _update_lt_rankings(self, lt_results: dict):
        """Upsert LT metrics into StrategyRanking and re-blend composite_score."""
        try:
            from app.database import async_session
            from app.models import StrategyRanking
            from sqlalchemy import select

            async with async_session() as db:
                for strat_name, result in lt_results.items():
                    lt_composite = _compute_lt_composite(result)

                    stmt = select(StrategyRanking).where(
                        StrategyRanking.strategy_name == strat_name
                    )
                    row = (await db.execute(stmt)).scalar_one_or_none()

                    if row is None:
                        # Strategy has no ST ranking yet — create a skeleton row
                        row = StrategyRanking(
                            strategy_name=strat_name,
                            st_composite_score=0.0,
                            composite_score=lt_composite,
                        )
                        db.add(row)

                    # Write LT metrics
                    row.lt_cagr_pct         = round(result.cagr_pct, 4)
                    row.lt_sharpe           = round(result.sharpe_ratio, 4)
                    row.lt_sortino          = round(result.sortino_ratio, 4)
                    row.lt_calmar           = round(result.calmar_ratio, 4)
                    row.lt_max_drawdown_pct = round(result.max_drawdown_pct, 4)
                    row.lt_win_rate         = round(result.win_rate, 4)
                    row.lt_profit_factor    = round(result.profit_factor, 4)
                    row.lt_total_trades     = result.total_trades
                    row.lt_years_tested     = round(result.years_tested, 2)
                    row.lt_composite_score  = lt_composite
                    row.lt_computed_at      = datetime.now(timezone.utc)

                    # Re-blend: 55% ST + 45% LT.
                    # LT-only strategies have no 1-min ST model — use lt_composite directly.
                    if strat_name in LT_ONLY_STRATEGIES:
                        row.composite_score = round(lt_composite, 2)
                    else:
                        st = row.st_composite_score if row.st_composite_score is not None else 0.0
                        row.composite_score = _blend(st, lt_composite)

                await db.commit()
                logger.info(
                    f"LT rankings updated for {len(lt_results)} strategies. "
                    f"Blended composite_scores now include 45% LT weight."
                )
        except Exception as e:
            logger.error(f"AutoBacktester: LT ranking update failed: {e}")


# Singleton
auto_backtester = AutoBacktester()
