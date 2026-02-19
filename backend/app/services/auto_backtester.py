"""Automated background backtester — runs on startup and every 4 hours.

Tests each strategy individually + curated combinations across 3 date ranges.
Computes composite rankings and stores results in DB.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.backtester import Backtester, STRATEGY_MAP

logger = logging.getLogger(__name__)

ALL_STRATEGIES = list(STRATEGY_MAP.keys())

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

    @property
    def progress(self) -> dict:
        return dict(self._progress)

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
        logger.info("AutoBacktester stopped")

    async def trigger(self):
        """Manually trigger a backtest run."""
        if self._progress["status"] == "running":
            return
        asyncio.create_task(self._run_backtests())

    async def _run_loop(self):
        """Run backtests on startup (after delay) then every 4 hours."""
        try:
            await asyncio.sleep(5)  # Let server finish starting
            while self._running:
                await self._run_backtests()
                # Wait 4 hours
                for _ in range(4 * 60 * 2):  # check every 30s for cancellation
                    if not self._running:
                        return
                    await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    async def _run_backtests(self):
        """Execute all backtests and compute rankings."""
        date_ranges = _date_ranges()

        # Build test list
        tests: list[dict] = []
        for strat in ALL_STRATEGIES:
            for dr in date_ranges:
                tests.append({
                    "strategies": [strat],
                    "label": f"{strat} ({dr['label']})",
                    **dr,
                })
        for combo in COMBO_CONFIGS:
            for dr in date_ranges:
                combo_name = "+".join(combo[:3]) + ("..." if len(combo) > 3 else "")
                tests.append({
                    "strategies": combo,
                    "label": f"{combo_name} ({dr['label']})",
                    **dr,
                })

        self._progress = {
            "status": "running",
            "current_test": "",
            "completed": 0,
            "total": len(tests),
            "errors": 0,
            "last_run": None,
            "results": [],
        }

        logger.info(f"AutoBacktester: starting {len(tests)} tests")
        results = []

        for i, test in enumerate(tests):
            if not self._running:
                break

            self._progress["current_test"] = test["label"]
            try:
                bt = Backtester(
                    strategies=test["strategies"],
                    use_regime_filter=True,
                )
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

                result_dict = result.to_dict()
                result_dict["strategies"] = ",".join(test["strategies"])
                result_dict["date_range"] = test["label"]
                result_dict["start_date"] = test["start"]
                result_dict["end_date"] = test["end"]
                result_dict["interval"] = test["interval"]
                results.append(result_dict)

                # Save to DB
                await self._save_result(test, result)

            except Exception as e:
                logger.warning(f"AutoBacktester: test '{test['label']}' failed: {e}")
                self._progress["errors"] += 1

            self._progress["completed"] = i + 1

            # Rate limiting delay
            await asyncio.sleep(1)

        # Compute rankings
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
        """Save individual backtest result to DB."""
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
        """Compute composite rankings from all individual strategy results."""
        try:
            from app.database import async_session
            from app.models import StrategyRanking

            # Aggregate per strategy (individual tests only)
            strategy_stats: dict[str, list[dict]] = {}
            for r in results:
                strats = r.get("strategies", "")
                # Only rank individual strategy tests
                if "," not in strats:
                    if strats not in strategy_stats:
                        strategy_stats[strats] = []
                    strategy_stats[strats].append(r)

            async with async_session() as db:
                # Clear old rankings
                from sqlalchemy import delete
                await db.execute(delete(StrategyRanking))

                for strat_name, runs in strategy_stats.items():
                    if not runs:
                        continue

                    total_trades = sum(r.get("total_trades", 0) for r in runs)

                    def safe_avg(key):
                        vals = [r[key] for r in runs if r.get(key) is not None]
                        return sum(vals) / len(vals) if vals else 0.0

                    avg_sharpe = safe_avg("sharpe_ratio")
                    avg_pf = safe_avg("profit_factor")
                    avg_wr = safe_avg("win_rate")
                    avg_ret = safe_avg("total_return_pct")
                    avg_dd = safe_avg("max_drawdown_pct")

                    # Composite score: 35% sharpe + 25% pf + 20% wr + 10% ret + 10% low dd
                    # Normalize each component to a 0-100 scale
                    sharpe_score = min(max(avg_sharpe / 3.0, -1), 1) * 100  # -3..3 -> -100..100
                    pf_score = min(max((avg_pf - 1.0) / 2.0, -1), 1) * 100  # 0..3 -> -50..100
                    wr_score = avg_wr * 100  # 0..1 -> 0..100
                    ret_score = min(max(avg_ret / 5.0, -1), 1) * 100  # -5%..5% -> -100..100
                    dd_score = max(0, 100 - avg_dd * 10)  # lower DD = higher score

                    composite = (
                        0.35 * sharpe_score
                        + 0.25 * pf_score
                        + 0.20 * wr_score
                        + 0.10 * ret_score
                        + 0.10 * dd_score
                    )

                    ranking = StrategyRanking(
                        strategy_name=strat_name,
                        avg_sharpe_ratio=round(avg_sharpe, 4),
                        avg_profit_factor=round(avg_pf, 4),
                        avg_win_rate=round(avg_wr, 4),
                        avg_return_pct=round(avg_ret, 4),
                        avg_max_drawdown_pct=round(avg_dd, 4),
                        composite_score=round(composite, 2),
                        total_backtest_trades=total_trades,
                        backtest_count=len(runs),
                        computed_at=datetime.now(timezone.utc),
                    )
                    db.add(ranking)

                await db.commit()
                logger.info(f"AutoBacktester: computed rankings for {len(strategy_stats)} strategies")

        except Exception as e:
            logger.error(f"AutoBacktester: failed to compute rankings: {e}")


# Singleton
auto_backtester = AutoBacktester()
