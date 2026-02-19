import { useLeaderboard } from '../hooks/useLeaderboard';
import { triggerBacktests } from '../services/api';

const STRATEGY_LABELS: Record<string, string> = {
  vwap_reversion: 'VWAP Reversion',
  orb: 'ORB',
  ema_crossover: 'EMA Crossover',
  volume_flow: 'Volume Flow',
  mtf_momentum: 'MTF Momentum',
  rsi_divergence: 'RSI Divergence',
  bb_squeeze: 'BB Squeeze',
  macd_reversal: 'MACD Reversal',
  momentum_scalper: 'Mom. Scalper',
  gap_fill: 'Gap Fill',
  micro_pullback: 'Micro Pullback',
  double_bottom_top: 'Dbl Bot/Top',
};

const RANK_COLORS = ['text-caution', 'text-terminal-300', 'text-orange-400'];

export default function StrategyLeaderboard() {
  const { rankings, progress, loading, refresh } = useLeaderboard();

  const isRunning = progress.status === 'running';
  const pct = progress.total > 0
    ? Math.round((progress.completed / progress.total) * 100)
    : 0;

  const handleTrigger = async () => {
    await triggerBacktests();
    setTimeout(refresh, 2000);
  };

  return (
    <div className="card p-4">
      <div className="card-header">
        <h2 className="card-title">Strategy Leaderboard</h2>
        <button
          onClick={handleTrigger}
          disabled={isRunning}
          className="btn-primary text-xs px-3 py-1.5"
        >
          {isRunning ? 'Running...' : 'Re-run Backtests'}
        </button>
      </div>

      {/* Progress bar */}
      {isRunning && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xxs text-muted">{progress.current_test}</span>
            <span className="text-xxs font-mono text-muted">{progress.completed}/{progress.total} ({pct}%)</span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill bg-gradient-to-r from-accent-dim to-accent"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {progress.status === 'complete' && progress.last_run && (
        <p className="text-xxs text-subtle mb-3">
          Last run: {new Date(progress.last_run).toLocaleString()}
          {progress.errors > 0 && ` (${progress.errors} errors)`}
        </p>
      )}

      {/* Rankings table */}
      {rankings.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Strategy</th>
                <th className="text-right">Score</th>
                <th className="text-right">Sharpe</th>
                <th className="text-right">PF</th>
                <th className="text-right">Win%</th>
                <th className="text-right">Return</th>
                <th className="text-right">Max DD</th>
                <th className="text-right">Trades</th>
              </tr>
            </thead>
            <tbody>
              {rankings.map((r, i) => (
                <tr key={r.strategy_name}>
                  <td className={`font-mono font-semibold ${RANK_COLORS[i] ?? 'text-muted'}`}>{i + 1}</td>
                  <td className="font-medium text-terminal-200">
                    {STRATEGY_LABELS[r.strategy_name] ?? r.strategy_name}
                  </td>
                  <td className={`text-right font-mono tabular-nums ${r.composite_score >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {r.composite_score.toFixed(1)}
                  </td>
                  <td className={`text-right font-mono tabular-nums ${r.avg_sharpe_ratio >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {r.avg_sharpe_ratio.toFixed(2)}
                  </td>
                  <td className={`text-right font-mono tabular-nums ${r.avg_profit_factor >= 1 ? 'text-profit' : 'text-loss'}`}>
                    {r.avg_profit_factor.toFixed(2)}
                  </td>
                  <td className={`text-right font-mono tabular-nums ${r.avg_win_rate >= 0.5 ? 'text-profit' : 'text-loss'}`}>
                    {(r.avg_win_rate * 100).toFixed(1)}%
                  </td>
                  <td className={`text-right font-mono tabular-nums ${r.avg_return_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {r.avg_return_pct.toFixed(2)}%
                  </td>
                  <td className="text-right font-mono tabular-nums text-loss">
                    {r.avg_max_drawdown_pct.toFixed(2)}%
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted">
                    {r.total_backtest_trades}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !loading && (
          <p className="text-sm text-muted py-6 text-center">
            {isRunning ? 'Backtests running...' : 'No rankings yet. Backtests will run automatically.'}
          </p>
        )
      )}
    </div>
  );
}
