"""Crisis-period backtester: stress-test strategies across historical crises."""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import Any

from app.services.long_term_backtester import LongTermBacktester, ALL_STRATEGIES

logger = logging.getLogger(__name__)

# ── Crisis and normal windows ─────────────────────────────────────────────────

CRISIS_WINDOWS = {
    "dot_com_crash": {
        "label": "Dot-com Crash",
        "start": "2000-03-01",
        "end":   "2002-10-09",
        "is_crisis": True,
    },
    "gfc": {
        "label": "Global Financial Crisis",
        "start": "2007-10-01",
        "end":   "2009-03-09",
        "is_crisis": True,
    },
    "covid_crash": {
        "label": "COVID-19 Crash",
        "start": "2020-02-19",
        "end":   "2020-04-30",
        "is_crisis": True,
    },
    "normal_2003_2006": {
        "label": "Post-Dot-com Bull",
        "start": "2003-01-01",
        "end":   "2006-12-31",
        "is_crisis": False,
    },
    "normal_2010_2019": {
        "label": "Long Bull Market",
        "start": "2010-01-01",
        "end":   "2019-12-31",
        "is_crisis": False,
    },
    "normal_post_covid": {
        "label": "Post-COVID Bull",
        "start": "2020-05-01",
        "end":   "2023-12-31",
        "is_crisis": False,
    },
}

# Stress scenarios applied only to crisis windows
STRESS_SCENARIOS = {
    "baseline":        {"slippage_mult": 1.0, "commission_mult": 1.0, "spread_cost_mult": 1.0},
    "double_slippage": {"slippage_mult": 2.0, "commission_mult": 1.0, "spread_cost_mult": 1.0},
    "triple_spreads":  {"slippage_mult": 1.0, "commission_mult": 1.0, "spread_cost_mult": 3.0},
    "full_stress":     {"slippage_mult": 2.0, "commission_mult": 2.0, "spread_cost_mult": 3.0},
}

BASELINE_SCENARIO = {"slippage_mult": 1.0, "commission_mult": 1.0, "spread_cost_mult": 1.0}


def _lt_result_to_dict(result) -> dict:
    """Convert LongTermResult to JSON-serialisable dict."""
    return {
        "cagr_pct":         result.cagr_pct,
        "sharpe_ratio":     result.sharpe_ratio,
        "sortino_ratio":    result.sortino_ratio,
        "calmar_ratio":     result.calmar_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "total_return_pct": result.total_return_pct,
        "win_rate":         result.win_rate,
        "total_trades":     result.total_trades,
        "profit_factor":    result.profit_factor,
        "avg_win":          result.avg_win,
        "avg_loss":         result.avg_loss,
        "final_capital":    result.final_capital,
        "years_tested":     result.years_tested,
        "equity_curve":     result.equity_curve,
        "yearly_returns":   result.yearly_returns,
    }


def _empty_result_dict() -> dict:
    return {
        "cagr_pct": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
        "calmar_ratio": 0.0, "max_drawdown_pct": 0.0, "total_return_pct": 0.0,
        "win_rate": 0.0, "total_trades": 0, "profit_factor": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "final_capital": 0.0,
        "years_tested": 0.0, "equity_curve": [], "yearly_returns": [],
    }


def _generate_report(
    window_results: dict[str, dict],
    per_strategy_baseline: dict[str, dict[str, dict]],
    strategies: list[str],
) -> dict:
    """
    Derive per-strategy rankings from individual strategy runs (per_strategy_baseline),
    and stress sensitivity from combined window results.

    per_strategy_baseline: { strategy → { window_id → result_dict } }
    window_results:         { window_id → { label, period, is_crisis, scenarios: { scenario_id → result_dict } } }
    """
    crisis_ids = [wid for wid, w in CRISIS_WINDOWS.items() if w["is_crisis"]]
    normal_ids = [wid for wid, w in CRISIS_WINDOWS.items() if not w["is_crisis"]]

    rankings: list[dict] = []
    for strat in strategies:
        strat_windows = per_strategy_baseline.get(strat, {})

        # Per-strategy Sharpe and DD across crisis windows
        crisis_sharpes: list[float] = []
        crisis_dds: list[float] = []
        for wid in crisis_ids:
            res = strat_windows.get(wid, _empty_result_dict())
            crisis_sharpes.append(res["sharpe_ratio"])
            crisis_dds.append(res["max_drawdown_pct"])

        # Per-strategy Sharpe across normal windows
        normal_sharpes: list[float] = []
        for wid in normal_ids:
            res = strat_windows.get(wid, _empty_result_dict())
            normal_sharpes.append(res["sharpe_ratio"])

        crisis_sharpe = sum(crisis_sharpes) / len(crisis_sharpes) if crisis_sharpes else 0.0
        avg_crisis_dd = sum(crisis_dds) / len(crisis_dds) if crisis_dds else 0.0
        normal_sharpe = sum(normal_sharpes) / len(normal_sharpes) if normal_sharpes else 0.0

        # Stress sensitivity from combined window results (full_stress vs baseline)
        stress_degradations: list[float] = []
        for wid in crisis_ids:
            wdata = window_results.get(wid, {})
            base_sharpe = wdata.get("scenarios", {}).get("baseline", {}).get("sharpe_ratio", 0.0)
            full_sharpe = wdata.get("scenarios", {}).get("full_stress", {}).get("sharpe_ratio", 0.0)
            if abs(base_sharpe) > 0.01:
                degradation = (base_sharpe - full_sharpe) / abs(base_sharpe)
                stress_degradations.append(degradation)

        avg_stress_degradation = (
            sum(stress_degradations) / len(stress_degradations)
            if stress_degradations else 0.0
        )

        # Assign badge
        is_resilient       = crisis_sharpe >= 0 and avg_crisis_dd < 20.0
        is_vulnerable      = crisis_sharpe < -0.5 or avg_crisis_dd > 30.0
        is_stress_sensitive = avg_stress_degradation > 0.5

        if is_resilient and not is_stress_sensitive:
            badge = "resilient"
        elif is_vulnerable:
            badge = "vulnerable"
        elif is_stress_sensitive:
            badge = "stress_sensitive"
        else:
            badge = "neutral"

        # Crisis composite: 0.6 × normalised crisis_sharpe + 0.4 × (1 − avg_dd/100)
        norm_crisis_sharpe = max(-1.0, min(1.0, crisis_sharpe))
        crisis_composite = 0.6 * norm_crisis_sharpe + 0.4 * (1.0 - avg_crisis_dd / 100.0)

        rankings.append({
            "strategy":         strat,
            "crisis_sharpe":    round(crisis_sharpe, 3),
            "normal_sharpe":    round(normal_sharpe, 3),
            "crisis_max_dd":    round(avg_crisis_dd, 2),
            "crisis_composite": round(crisis_composite, 3),
            "badge":            badge,
        })

    rankings.sort(key=lambda r: r["crisis_composite"], reverse=True)

    crisis_resilient  = [r["strategy"] for r in rankings if r["badge"] == "resilient"]
    crisis_vulnerable = [r["strategy"] for r in rankings if r["badge"] == "vulnerable"]
    stress_sensitive  = [r["strategy"] for r in rankings if r["badge"] == "stress_sensitive"]

    recommendations: dict[str, str] = {}
    action_plan: list[dict] = []
    for priority, r in enumerate(rankings, start=1):
        strat = r["strategy"]
        badge = r["badge"]
        cs    = r["crisis_sharpe"]
        dd    = r["crisis_max_dd"]
        ns    = r["normal_sharpe"]

        if badge == "resilient":
            rec = (
                f"{strat} performs well during crises (Sharpe {cs:+.2f}) "
                f"with controlled drawdown ({dd:.1f}%). "
                "Increase allocation weight during elevated VIX environments."
            )
            action = "KEEP"
        elif badge == "vulnerable":
            if dd > 30:
                failure = "excessive drawdown"
                fix = "Add hard 15% drawdown circuit-breaker during crisis regimes."
            elif cs < -0.5:
                failure = "consistent negative Sharpe"
                fix = "Disable during bear regimes; enable only when EMA200 is rising."
            else:
                failure = "poor crisis performance"
                fix = "Review signal logic; consider regime-aware position sizing."
            rec = (
                f"{strat} is vulnerable in crises (Sharpe {cs:+.2f}, MaxDD {dd:.1f}%) "
                f"due to {failure}. Normal Sharpe: {ns:+.2f}. Suggested fix: {fix}"
            )
            action = "RETIRE" if r["crisis_composite"] < 0.1 else "REFINE"
        elif badge == "stress_sensitive":
            rec = (
                f"{strat} degrades significantly under execution stress (liquidity crunch). "
                f"Crisis baseline Sharpe {cs:+.2f} but full-stress drops materially. "
                "Use smaller position sizes (50%) during VIX > 30 events."
            )
            action = "REFINE"
        else:
            rec = (
                f"{strat} shows neutral crisis behaviour (Sharpe {cs:+.2f}, MaxDD {dd:.1f}%). "
                f"Normal Sharpe {ns:+.2f}. Monitor drawdown limits during crisis windows."
            )
            action = "KEEP" if r["crisis_composite"] >= 0.1 else "REFINE"

        recommendations[strat] = rec
        action_plan.append({
            "priority": priority,
            "strategy": strat,
            "action":   action,
            "reason":   rec,
        })

    return {
        "crisis_resilient":  crisis_resilient,
        "crisis_vulnerable": crisis_vulnerable,
        "stress_sensitive":  stress_sensitive,
        "strategy_rankings": rankings,
        "recommendations":   recommendations,
        "action_plan":       action_plan,
    }


class CrisisBacktester:
    """
    Singleton that stress-tests strategies across historical crisis windows.

    Phase 1 (combined scenarios): Runs all strategies together for each window × scenario
              → stored in window_results for equity curve display.
    Phase 2 (per-strategy baseline): Runs each strategy individually (baseline only)
              → used for per-strategy rankings in the report.
    """

    def __init__(self) -> None:
        self._progress: dict[str, Any] = {
            "status":           "idle",
            "current_window":   "",
            "current_scenario": "",
            "completed":        0,
            "total":            0,
            "errors":           0,
            "last_run":         None,
        }
        self._result: dict | None = None
        self._task: asyncio.Task | None = None

    @property
    def progress(self) -> dict:
        return dict(self._progress)

    @property
    def result(self) -> dict | None:
        return self._result

    def is_running(self) -> bool:
        return self._progress["status"] == "running"

    async def run(
        self,
        strategies: list[str] | None = None,
        initial_capital: float = 50_000.0,
        windows: list[str] | None = None,
    ) -> None:
        """Start the crisis backtest as a background asyncio task."""
        if self.is_running():
            logger.info("CrisisBacktester already running — skipping duplicate request")
            return

        selected_strategies = strategies or list(ALL_STRATEGIES)
        selected_windows    = windows or list(CRISIS_WINDOWS.keys())

        # Phase 1: combined window × scenario runs
        combined_total = sum(
            len(STRESS_SCENARIOS) if CRISIS_WINDOWS[wid]["is_crisis"] else 1
            for wid in selected_windows
        )
        # Phase 2: per-strategy baseline runs (one per strategy per window)
        per_strat_total = len(selected_strategies) * len(selected_windows)
        total = combined_total + per_strat_total

        self._progress = {
            "status":           "running",
            "current_window":   "",
            "current_scenario": "",
            "completed":        0,
            "total":            total,
            "errors":           0,
            "last_run":         None,
        }

        self._task = asyncio.create_task(
            self._run_all(selected_strategies, initial_capital, selected_windows)
        )

    async def _run_one(
        self,
        strategies: list[str],
        initial_capital: float,
        start: str,
        end: str,
        slippage_mult: float = 1.0,
        commission_mult: float = 1.0,
        spread_cost_mult: float = 1.0,
    ) -> dict:
        loop = asyncio.get_event_loop()
        bt = LongTermBacktester(
            strategies=strategies,
            initial_capital=initial_capital,
            slippage_mult=slippage_mult,
            commission_mult=commission_mult,
            spread_cost_mult=spread_cost_mult,
        )
        result = await loop.run_in_executor(None, bt.run, "SPY", start, end, True)
        return _lt_result_to_dict(result)

    async def _run_all(
        self,
        strategies: list[str],
        initial_capital: float,
        selected_windows: list[str],
    ) -> None:
        window_results: dict[str, dict] = {}
        per_strategy_baseline: dict[str, dict[str, dict]] = {}
        errors = 0

        try:
            # ── Phase 1: Combined runs per window × scenario ──────────────────
            for wid in selected_windows:
                wcfg      = CRISIS_WINDOWS[wid]
                start     = wcfg["start"]
                end       = wcfg["end"]
                is_crisis = wcfg["is_crisis"]
                scenarios_to_run = STRESS_SCENARIOS if is_crisis else {"baseline": BASELINE_SCENARIO}

                window_results[wid] = {
                    "label":     wcfg["label"],
                    "period":    f"{start} → {end}",
                    "is_crisis": is_crisis,
                    "scenarios": {},
                }

                for scenario_id, scenario_cfg in scenarios_to_run.items():
                    self._progress["current_window"]   = wcfg["label"]
                    self._progress["current_scenario"] = f"combined/{scenario_id}"
                    try:
                        res = await self._run_one(
                            strategies, initial_capital, start, end, **scenario_cfg
                        )
                        window_results[wid]["scenarios"][scenario_id] = res
                    except Exception as exc:
                        logger.warning(
                            "Combined run error window=%s scenario=%s: %s", wid, scenario_id, exc
                        )
                        window_results[wid]["scenarios"][scenario_id] = _empty_result_dict()
                        errors += 1
                    self._progress["completed"] += 1
                    self._progress["errors"] = errors

            # ── Phase 2: Per-strategy baseline runs (for rankings) ─────────────
            for strat in strategies:
                per_strategy_baseline[strat] = {}
                for wid in selected_windows:
                    wcfg  = CRISIS_WINDOWS[wid]
                    start = wcfg["start"]
                    end   = wcfg["end"]
                    self._progress["current_window"]   = wcfg["label"]
                    self._progress["current_scenario"] = f"{strat}/baseline"
                    try:
                        res = await self._run_one(
                            [strat], initial_capital, start, end, **BASELINE_SCENARIO
                        )
                        per_strategy_baseline[strat][wid] = res
                    except Exception as exc:
                        logger.warning(
                            "Per-strategy run error strat=%s window=%s: %s", strat, wid, exc
                        )
                        per_strategy_baseline[strat][wid] = _empty_result_dict()
                        errors += 1
                    self._progress["completed"] += 1
                    self._progress["errors"] = errors

            report = _generate_report(window_results, per_strategy_baseline, strategies)

            self._result = {
                "windows": window_results,
                "report":  report,
                "data_note": (
                    "Results use yfinance daily OHLCV bars. "
                    "Slippage: 1 bp/side × mult. Commission: $0.005/share × mult. "
                    "Credit-spread premium reduced by spread_cost_mult (wider bid-ask). "
                    "Upgrade path: Polygon.io options add-on (~$79/mo) for real options data."
                ),
            }

            self._progress["status"]   = "done"
            self._progress["last_run"] = datetime.utcnow().isoformat()
            logger.info(
                "Crisis backtest completed: %d windows, %d strategies, %d errors",
                len(selected_windows), len(strategies), errors,
            )

        except Exception as exc:
            logger.exception("Crisis backtester fatal error: %s", exc)
            self._progress["status"] = "error"
            self._progress["errors"] = errors + 1


crisis_backtester = CrisisBacktester()
