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
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Strategy Leaderboard</h2>
        <button
          onClick={handleTrigger}
          disabled={isRunning}
          className="px-3 py-1 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 rounded text-xs font-medium transition"
        >
          {isRunning ? 'Running...' : 'Re-run Backtests'}
        </button>
      </div>

      {/* Progress bar */}
      {isRunning && (
        <div className="mb-3">
          <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
            <span>{progress.current_test}</span>
            <span>{progress.completed}/{progress.total} ({pct}%)</span>
          </div>
          <div className="w-full bg-gray-800 rounded-full h-2">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {progress.status === 'complete' && progress.last_run && (
        <p className="text-xs text-gray-500 mb-3">
          Last run: {new Date(progress.last_run).toLocaleString()}
          {progress.errors > 0 && ` (${progress.errors} errors)`}
        </p>
      )}

      {/* Rankings table */}
      {rankings.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-gray-500 border-b border-gray-800">
              <tr>
                <th className="px-2 py-1.5 text-left">#</th>
                <th className="px-2 py-1.5 text-left">Strategy</th>
                <th className="px-2 py-1.5 text-right">Score</th>
                <th className="px-2 py-1.5 text-right">Sharpe</th>
                <th className="px-2 py-1.5 text-right">PF</th>
                <th className="px-2 py-1.5 text-right">Win%</th>
                <th className="px-2 py-1.5 text-right">Return</th>
                <th className="px-2 py-1.5 text-right">Max DD</th>
                <th className="px-2 py-1.5 text-right">Trades</th>
              </tr>
            </thead>
            <tbody>
              {rankings.map((r, i) => (
                <tr key={r.strategy_name} className="border-t border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-2 py-1.5 font-mono text-gray-500">{i + 1}</td>
                  <td className="px-2 py-1.5 font-medium">
                    {STRATEGY_LABELS[r.strategy_name] ?? r.strategy_name}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${r.composite_score >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.composite_score.toFixed(1)}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${r.avg_sharpe_ratio >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.avg_sharpe_ratio.toFixed(2)}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${r.avg_profit_factor >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.avg_profit_factor.toFixed(2)}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${r.avg_win_rate >= 0.5 ? 'text-green-400' : 'text-red-400'}`}>
                    {(r.avg_win_rate * 100).toFixed(1)}%
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${r.avg_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.avg_return_pct.toFixed(2)}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-red-400">
                    {r.avg_max_drawdown_pct.toFixed(2)}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-gray-400">
                    {r.total_backtest_trades}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !loading && (
          <p className="text-sm text-gray-500 py-4 text-center">
            {isRunning ? 'Backtests running...' : 'No rankings yet. Backtests will run automatically.'}
          </p>
        )
      )}
    </div>
  );
}
