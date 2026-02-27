"""
StrategyMonitor: per-strategy live performance tracking that feeds back into
strategy selection and auto-disables consistently underperforming strategies.

Design:
  - In-memory dict as primary store (fast, no async needed for reads)
  - DB (StrategyLivePerformance) as durable backing store
  - Loaded at startup, saved after every trade close
  - Provides blended backtest+live score used in _check_entries weighting
  - Auto-disables strategies exceeding loss thresholds; re-enables after 24h
    if the backtest composite_score is still positive (>= 10)
"""

from __future__ import annotations
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

ET_UTC_OFFSET = 0  # we store datetimes in UTC

# ── Auto-disable thresholds ───────────────────────────────────────────────────
CONSECUTIVE_LOSS_THRESHOLD = 5    # disable after N live losses in a row
MIN_TRADES_FOR_WIN_RATE_DISABLE = 20  # require at least this many before judging
WIN_RATE_DISABLE_THRESHOLD = 0.35     # disable if live win rate drops below 35%
RE_ENABLE_COOLDOWN_HOURS = 24         # re-enable after 24h if backtest still healthy
RE_ENABLE_MIN_BACKTEST_SCORE = 10.0   # minimum backtest composite_score to re-enable

# ── Live-score blending weights (based on sample size) ───────────────────────
# < 10 live trades:  pure backtest
# 10-30:             15% live weight
# 30-50:             30% live weight
# 50+:               45% live weight
_LIVE_WEIGHT_TIERS = [
    (10,  0.00),   # < 10 trades: no live weight
    (30,  0.15),   # 10-29
    (50,  0.30),   # 30-49
    (9999,0.45),   # 50+
]


@dataclass
class StrategyStats:
    strategy_name: str
    live_trades: int = 0
    live_wins: int = 0
    live_losses: int = 0
    live_pnl_total: float = 0.0
    live_win_rate: float = 0.0
    live_avg_win: float = 0.0
    live_avg_loss: float = 0.0
    live_profit_factor: float = 0.0
    consecutive_live_losses: int = 0
    auto_disabled: bool = False
    disabled_reason: Optional[str] = None
    disabled_at: Optional[datetime] = None
    last_trade_at: Optional[datetime] = None
    _win_pnl_list: list[float] = field(default_factory=list)
    _loss_pnl_list: list[float] = field(default_factory=list)

    def record(self, pnl: float):
        self.live_trades += 1
        self.last_trade_at = datetime.now(timezone.utc)
        if pnl > 0:
            self.live_wins += 1
            self.consecutive_live_losses = 0
            self._win_pnl_list.append(pnl)
        else:
            self.live_losses += 1
            self.consecutive_live_losses += 1
            self._loss_pnl_list.append(pnl)

        self.live_pnl_total += pnl
        self.live_win_rate = self.live_wins / self.live_trades if self.live_trades > 0 else 0.0
        self.live_avg_win = sum(self._win_pnl_list) / len(self._win_pnl_list) if self._win_pnl_list else 0.0
        self.live_avg_loss = sum(self._loss_pnl_list) / len(self._loss_pnl_list) if self._loss_pnl_list else 0.0

        gross_profit = sum(self._win_pnl_list)
        gross_loss = abs(sum(self._loss_pnl_list)) if self._loss_pnl_list else 0.0
        self.live_profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "live_trades": self.live_trades,
            "live_wins": self.live_wins,
            "live_losses": self.live_losses,
            "live_pnl_total": round(self.live_pnl_total, 2),
            "live_win_rate": round(self.live_win_rate, 4),
            "live_avg_win": round(self.live_avg_win, 2),
            "live_avg_loss": round(self.live_avg_loss, 2),
            "live_profit_factor": round(self.live_profit_factor, 3),
            "consecutive_live_losses": self.consecutive_live_losses,
            "auto_disabled": self.auto_disabled,
            "disabled_reason": self.disabled_reason,
            "disabled_at": self.disabled_at.isoformat() if self.disabled_at else None,
            "last_trade_at": self.last_trade_at.isoformat() if self.last_trade_at else None,
        }


class StrategyMonitor:
    """
    Central authority for per-strategy live performance.

    Key contracts:
    - record_trade(strategy, pnl): call after every closed trade
    - get_blended_score(strategy, backtest_score): use in _check_entries weighting
    - is_auto_disabled(strategy): gate in _check_entries allowed list
    - async load_from_db(db): call at startup
    - async save_to_db(strategy, db): call after record_trade
    - async check_and_reenable(strategy, backtest_score, db): call in score refresh
    """

    def __init__(self):
        self._stats: dict[str, StrategyStats] = {}

    def _get_or_create(self, strategy: str) -> StrategyStats:
        if strategy not in self._stats:
            self._stats[strategy] = StrategyStats(strategy_name=strategy)
        return self._stats[strategy]

    # ── Core API ─────────────────────────────────────────────────────────────

    def record_trade(self, strategy: str, pnl: float):
        """Update in-memory stats synchronously. Call save_to_db afterward."""
        st = self._get_or_create(strategy)
        st.record(pnl)
        logger.info(
            f"StrategyMonitor [{strategy}]: trade #{st.live_trades} "
            f"P&L=${pnl:.2f} | live_wr={st.live_win_rate:.0%} "
            f"| consec_loss={st.consecutive_live_losses}"
        )

    def should_auto_disable(self, strategy: str) -> tuple[bool, str]:
        """Return (should_disable, reason). Call after record_trade."""
        st = self._stats.get(strategy)
        if st is None or st.auto_disabled:
            return False, ""

        if st.consecutive_live_losses >= CONSECUTIVE_LOSS_THRESHOLD:
            return True, f"{CONSECUTIVE_LOSS_THRESHOLD} consecutive live losses"

        if (st.live_trades >= MIN_TRADES_FOR_WIN_RATE_DISABLE
                and st.live_win_rate < WIN_RATE_DISABLE_THRESHOLD):
            return True, (
                f"Live win rate {st.live_win_rate:.0%} < {WIN_RATE_DISABLE_THRESHOLD:.0%} "
                f"over {st.live_trades} trades"
            )

        return False, ""

    def mark_disabled(self, strategy: str, reason: str):
        st = self._get_or_create(strategy)
        st.auto_disabled = True
        st.disabled_reason = reason
        st.disabled_at = datetime.now(timezone.utc)
        logger.warning(f"StrategyMonitor: auto-disabled [{strategy}] — {reason}")

    async def auto_disable_strategy(self, strategy: str, reason: str, db) -> None:
        """
        Disable a strategy from the backtest retirement pipeline and persist to DB.
        Idempotent — safe to call repeatedly with the same strategy.
        """
        st = self._get_or_create(strategy)
        if st.auto_disabled:
            return  # already disabled, no-op
        st.auto_disabled = True
        st.disabled_reason = reason
        st.disabled_at = datetime.now(timezone.utc)
        logger.warning(
            f"StrategyMonitor: retirement auto-disable [{strategy}] — {reason}"
        )
        await self.save_to_db(strategy, db)

    def is_auto_disabled(self, strategy: str) -> bool:
        st = self._stats.get(strategy)
        return st.auto_disabled if st else False

    def get_blended_score(self, strategy: str, backtest_score: float) -> float:
        """
        Blend backtest composite_score (−20..100) with live performance score.

        Live score uses same −20..100 scale:
          - win_rate 50% → 50pts, 65% → 81pts, 35% → 18pts
          - profit_factor 1.5 → 50pts, 2.5 → 100pts, 0.5 → −50pts (clamped −20)

        Blending weight increases with live sample size.
        """
        st = self._stats.get(strategy)
        if st is None or st.live_trades == 0:
            return backtest_score

        # Determine live weight tier
        live_weight = 0.0
        for threshold, weight in _LIVE_WEIGHT_TIERS:
            if st.live_trades < threshold:
                live_weight = weight
                break

        if live_weight == 0.0:
            return backtest_score

        # Live score (−20..100 scale matching composite_score)
        wr_score = (st.live_win_rate - 0.5) * 200      # 50%→0, 65%→30, 35%→−30
        pf = st.live_profit_factor
        pf_score = (pf - 1.0) * 50                     # 1.0→0, 2.0→50, 0.5→−25
        live_raw = wr_score * 0.6 + pf_score * 0.4
        live_score = max(-20.0, min(100.0, live_raw))

        backtest_weight = 1.0 - live_weight
        blended = backtest_score * backtest_weight + live_score * live_weight

        logger.debug(
            f"StrategyMonitor blended [{strategy}]: "
            f"bt={backtest_score:.1f}×{backtest_weight:.0%} + "
            f"live={live_score:.1f}×{live_weight:.0%} = {blended:.1f}"
        )
        return blended

    def all_stats(self) -> dict[str, dict]:
        return {k: v.to_dict() for k, v in self._stats.items()}

    def get_stats(self, strategy: str) -> dict:
        st = self._stats.get(strategy)
        return st.to_dict() if st else {}

    # ── DB persistence ────────────────────────────────────────────────────────

    async def load_from_db(self, db) -> None:
        """Load all strategy live stats from DB at startup."""
        try:
            from sqlalchemy import select
            from app.models import StrategyLivePerformance
            result = await db.execute(select(StrategyLivePerformance))
            rows = result.scalars().all()
            for row in rows:
                st = StrategyStats(strategy_name=row.strategy_name)
                st.live_trades = row.live_trades or 0
                st.live_wins = row.live_wins or 0
                st.live_losses = row.live_losses or 0
                st.live_pnl_total = row.live_pnl_total or 0.0
                st.live_win_rate = row.live_win_rate or 0.0
                st.live_avg_win = row.live_avg_win or 0.0
                st.live_avg_loss = row.live_avg_loss or 0.0
                st.live_profit_factor = row.live_profit_factor or 0.0
                st.consecutive_live_losses = row.consecutive_live_losses or 0
                st.auto_disabled = bool(row.auto_disabled)
                st.disabled_reason = row.disabled_reason
                st.disabled_at = row.disabled_at
                st.last_trade_at = row.last_trade_at
                self._stats[row.strategy_name] = st
            logger.info(f"StrategyMonitor: loaded {len(rows)} strategy profiles from DB")
        except Exception as e:
            logger.warning(f"StrategyMonitor: could not load from DB: {e}")

    async def save_to_db(self, strategy: str, db) -> None:
        """Upsert a single strategy's live stats to DB."""
        st = self._stats.get(strategy)
        if st is None:
            return
        try:
            from sqlalchemy import select
            from app.models import StrategyLivePerformance
            result = await db.execute(
                select(StrategyLivePerformance).where(
                    StrategyLivePerformance.strategy_name == strategy
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = StrategyLivePerformance(strategy_name=strategy)
                db.add(row)

            row.live_trades = st.live_trades
            row.live_wins = st.live_wins
            row.live_losses = st.live_losses
            row.live_pnl_total = st.live_pnl_total
            row.live_win_rate = st.live_win_rate
            row.live_avg_win = st.live_avg_win
            row.live_avg_loss = st.live_avg_loss
            row.live_profit_factor = st.live_profit_factor
            row.consecutive_live_losses = st.consecutive_live_losses
            row.auto_disabled = st.auto_disabled
            row.disabled_reason = st.disabled_reason
            row.disabled_at = st.disabled_at
            row.last_trade_at = st.last_trade_at
            await db.commit()
        except Exception as e:
            logger.error(f"StrategyMonitor: DB save failed for {strategy}: {e}")

    async def check_and_reenable(
        self, strategy: str, backtest_score: float, db
    ) -> bool:
        """
        Re-enable a strategy after its 24h cooldown if backtest score is healthy.
        Returns True if the strategy was re-enabled.
        """
        st = self._stats.get(strategy)
        if st is None or not st.auto_disabled:
            return False

        if st.disabled_at is None:
            return False

        # Make disabled_at timezone-aware if needed
        disabled_at = st.disabled_at
        if disabled_at.tzinfo is None:
            disabled_at = disabled_at.replace(tzinfo=timezone.utc)

        age_hours = (datetime.now(timezone.utc) - disabled_at).total_seconds() / 3600
        if age_hours < RE_ENABLE_COOLDOWN_HOURS:
            return False

        if backtest_score < RE_ENABLE_MIN_BACKTEST_SCORE:
            logger.info(
                f"StrategyMonitor: [{strategy}] still disabled — backtest score "
                f"{backtest_score:.1f} < {RE_ENABLE_MIN_BACKTEST_SCORE} required for re-enable"
            )
            return False

        # Re-enable
        st.auto_disabled = False
        st.disabled_reason = None
        st.disabled_at = None
        st.consecutive_live_losses = 0   # Reset streak after cooldown
        await self.save_to_db(strategy, db)
        logger.info(
            f"StrategyMonitor: [{strategy}] re-enabled after {age_hours:.1f}h "
            f"cooldown (backtest score: {backtest_score:.1f})"
        )
        return True


# Singleton
strategy_monitor = StrategyMonitor()
