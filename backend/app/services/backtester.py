"""Bar-by-bar backtesting engine with performance metrics."""

from __future__ import annotations
from datetime import datetime, time, timedelta
from typing import Optional
import pandas as pd
import numpy as np
import logging

from app.services.data_manager import DataManager
from app.services.strategies.base import BaseStrategy, TradeSignal, Direction
from app.services.strategies.regime_detector import RegimeDetector, MarketRegime
from app.services.strategies.vwap_reversion import VWAPReversionStrategy
from app.services.strategies.orb import ORBStrategy
from app.services.strategies.ema_crossover import EMACrossoverStrategy

logger = logging.getLogger(__name__)

STRATEGY_MAP = {
    "vwap_reversion": VWAPReversionStrategy,
    "orb": ORBStrategy,
    "ema_crossover": EMACrossoverStrategy,
}

# Regime -> preferred strategies
REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: ["orb", "ema_crossover"],
    MarketRegime.TRENDING_DOWN: ["orb", "ema_crossover"],
    MarketRegime.RANGE_BOUND: ["vwap_reversion"],
    MarketRegime.VOLATILE: ["vwap_reversion"],  # tight stops
}


class BacktestResult:
    def __init__(self):
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.initial_capital: float = 0
        self.final_capital: float = 0

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t["pnl"] > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t["pnl"] <= 0)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return ((self.final_capital - self.initial_capital) / self.initial_capital) * 100

    @property
    def avg_win(self) -> float:
        wins = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        return np.mean(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t["pnl"] for t in self.trades if t["pnl"] <= 0]
        return np.mean(losses) if losses else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.trades if t["pnl"] < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        equities = [e["equity"] for e in self.equity_curve]
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd * 100

    @property
    def sharpe_ratio(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        equities = [e["equity"] for e in self.equity_curve]
        returns = pd.Series(equities).pct_change().dropna()
        if returns.std() == 0:
            return 0.0
        # Annualize assuming ~252 trading days, ~78 5-min bars per day
        daily_returns = returns.mean()
        daily_std = returns.std()
        return (daily_returns / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "initial_capital": self.initial_capital,
            "final_capital": round(self.final_capital, 2),
        }


class Backtester:
    """Bar-by-bar backtesting engine."""

    def __init__(
        self,
        strategies: list[str],
        initial_capital: float = 25000.0,
        max_risk_per_trade: float = 0.015,
        use_regime_filter: bool = True,
        max_trades_per_day: int = 10,
        daily_loss_limit: float = 0.02,
    ):
        self.initial_capital = initial_capital
        self.max_risk_per_trade = max_risk_per_trade
        self.use_regime_filter = use_regime_filter
        self.max_trades_per_day = max_trades_per_day
        self.daily_loss_limit = daily_loss_limit

        self.strategy_instances: dict[str, BaseStrategy] = {}
        for name in strategies:
            cls = STRATEGY_MAP.get(name)
            if cls:
                self.strategy_instances[name] = cls()

        self.regime_detector = RegimeDetector()

    def run(
        self,
        symbol: str = "SPY",
        start_date: str = "",
        end_date: str = "",
        interval: str = "1m",
    ) -> BacktestResult:
        logger.info(f"Starting backtest: {symbol} {start_date} to {end_date}")

        dm = DataManager()
        if start_date and end_date:
            df = dm.fetch_intraday(symbol, start=start_date, end=end_date, interval=interval)
        else:
            df = dm.fetch_intraday(symbol, period="5d", interval=interval)

        if df.empty:
            logger.warning("No data for backtest")
            result = BacktestResult()
            result.initial_capital = self.initial_capital
            result.final_capital = self.initial_capital
            return result

        df = dm.add_indicators(df)

        # Also prepare 5-min bars for regime detection + EMA crossover
        df_5min = dm.resample_to_5min(df)

        return self._simulate(df, df_5min)

    def _simulate(self, df: pd.DataFrame, df_5min: pd.DataFrame) -> BacktestResult:
        result = BacktestResult()
        result.initial_capital = self.initial_capital
        capital = self.initial_capital
        peak_capital = capital

        open_trade: Optional[dict] = None
        highest_since_entry = 0.0
        lowest_since_entry = float("inf")

        daily_trades = 0
        daily_pnl = 0.0
        current_date = None

        for idx in range(30, len(df)):
            bar = df.iloc[idx]
            bar_time = df.index[idx]
            if not hasattr(bar_time, 'date'):
                continue

            bar_date = bar_time.date()
            close = bar["close"]

            # Reset daily counters
            if bar_date != current_date:
                current_date = bar_date
                daily_trades = 0
                daily_pnl = 0.0
                # Reset ORB opening ranges for new day
                for s in self.strategy_instances.values():
                    if hasattr(s, '_opening_ranges'):
                        s._opening_ranges = {}

            # Skip outside market hours
            t = bar_time.time() if hasattr(bar_time, 'time') else None
            if t is None or t < time(9, 30) or t >= time(16, 0):
                continue

            # Determine regime from 5-min bars
            regime = MarketRegime.RANGE_BOUND
            if self.use_regime_filter and len(df_5min) > 20:
                # Find nearest 5-min bar
                five_min_idx = df_5min.index.searchsorted(bar_time) - 1
                if 0 <= five_min_idx < len(df_5min):
                    regime = self.regime_detector.detect(df_5min, five_min_idx)

            # Check exits for open trade
            if open_trade is not None:
                highest_since_entry = max(highest_since_entry, bar["high"])
                lowest_since_entry = min(lowest_since_entry, bar["low"])

                strategy = self.strategy_instances.get(open_trade["strategy"])
                if strategy:
                    exit_signal = strategy.should_exit(
                        df, idx, open_trade["signal"],
                        open_trade["entry_time"], bar_time,
                        highest_since_entry, lowest_since_entry,
                    )
                    if exit_signal:
                        pnl = self._calc_pnl(
                            open_trade["signal"], exit_signal.exit_price,
                            open_trade["quantity"]
                        )
                        capital += pnl
                        daily_pnl += pnl
                        peak_capital = max(peak_capital, capital)

                        result.trades.append({
                            "strategy": open_trade["strategy"],
                            "direction": open_trade["signal"].direction.value,
                            "entry_price": open_trade["signal"].entry_price,
                            "exit_price": exit_signal.exit_price,
                            "entry_time": str(open_trade["entry_time"]),
                            "exit_time": str(bar_time),
                            "quantity": open_trade["quantity"],
                            "pnl": round(pnl, 2),
                            "exit_reason": exit_signal.reason.value,
                            "regime": regime.value,
                        })
                        open_trade = None

            # Record equity
            unrealized = 0
            if open_trade:
                unrealized = self._calc_pnl(
                    open_trade["signal"], close, open_trade["quantity"]
                )
            result.equity_curve.append({
                "timestamp": str(bar_time),
                "equity": round(capital + unrealized, 2),
            })

            # Check daily limits
            if daily_pnl <= -(self.daily_loss_limit * self.initial_capital):
                continue
            if daily_trades >= self.max_trades_per_day:
                continue

            # Circuit breaker
            drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            if drawdown >= 0.16:
                continue

            # Try to open new trade
            if open_trade is None:
                allowed_strategies = list(self.strategy_instances.keys())
                if self.use_regime_filter:
                    allowed_strategies = [
                        s for s in allowed_strategies
                        if s in REGIME_STRATEGY_MAP.get(regime, [])
                    ]

                for strat_name in allowed_strategies:
                    strategy = self.strategy_instances[strat_name]
                    # Use 5-min bars for EMA crossover, 1-min for others
                    if strat_name == "ema_crossover" and len(df_5min) > 30:
                        five_idx = df_5min.index.searchsorted(bar_time) - 1
                        if five_idx < 30 or five_idx >= len(df_5min):
                            continue
                        signal = strategy.generate_signal(df_5min, five_idx, bar_time)
                    else:
                        signal = strategy.generate_signal(df, idx, bar_time)

                    if signal:
                        # Position sizing
                        risk_amount = capital * self.max_risk_per_trade
                        stop_dist = abs(signal.entry_price - signal.stop_loss)
                        if stop_dist <= 0:
                            continue
                        quantity = int(risk_amount / stop_dist)
                        if quantity <= 0:
                            continue
                        # Position cap: max 30% of capital
                        max_shares = int((capital * 0.30) / signal.entry_price)
                        quantity = min(quantity, max_shares)
                        if quantity <= 0:
                            continue

                        signal.quantity = quantity
                        open_trade = {
                            "signal": signal,
                            "strategy": strat_name,
                            "entry_time": bar_time,
                            "quantity": quantity,
                        }
                        highest_since_entry = bar["high"]
                        lowest_since_entry = bar["low"]
                        daily_trades += 1
                        break

        # Close any remaining open trade at last bar
        if open_trade and len(df) > 0:
            last_close = df.iloc[-1]["close"]
            pnl = self._calc_pnl(open_trade["signal"], last_close, open_trade["quantity"])
            capital += pnl
            result.trades.append({
                "strategy": open_trade["strategy"],
                "direction": open_trade["signal"].direction.value,
                "entry_price": open_trade["signal"].entry_price,
                "exit_price": last_close,
                "entry_time": str(open_trade["entry_time"]),
                "exit_time": str(df.index[-1]),
                "quantity": open_trade["quantity"],
                "pnl": round(pnl, 2),
                "exit_reason": "eod",
                "regime": "unknown",
            })

        result.final_capital = round(capital, 2)

        # Downsample equity curve if too large (keep every 5th point)
        if len(result.equity_curve) > 2000:
            step = len(result.equity_curve) // 1000
            result.equity_curve = result.equity_curve[::step]

        logger.info(
            f"Backtest complete: {result.total_trades} trades, "
            f"win rate={result.win_rate:.1%}, "
            f"return={result.total_return_pct:.2f}%, "
            f"max_dd={result.max_drawdown_pct:.2f}%"
        )
        return result

    @staticmethod
    def _calc_pnl(signal: TradeSignal, exit_price: float, quantity: int) -> float:
        if signal.direction == Direction.LONG:
            return (exit_price - signal.entry_price) * quantity
        else:
            return (signal.entry_price - exit_price) * quantity
