import { useState, useEffect } from 'react';
import { useBacktest } from '../hooks/useBacktest';
import Chart from './Chart';

export default function BacktestPanel() {
  const { results, current, loading, error, run, loadHistory } = useBacktest();

  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [strategy, setStrategy] = useState('vwap_reversion');
  const [useRegime, setUseRegime] = useState(true);
  const [capital, setCapital] = useState(25000);

  useEffect(() => {
    loadHistory();
    // Default dates: last 5 trading days
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 7);
    setStartDate(start.toISOString().split('T')[0]);
    setEndDate(end.toISOString().split('T')[0]);
  }, [loadHistory]);

  const handleRun = () => {
    run({
      start_date: startDate,
      end_date: endDate,
      strategies: [strategy],
      use_regime_filter: useRegime,
      initial_capital: capital,
    });
  };

  const allStrategies = [
    'vwap_reversion', 'orb', 'ema_crossover', 'volume_flow', 'mtf_momentum',
    'rsi_divergence', 'bb_squeeze', 'macd_reversal', 'momentum_scalper',
    'gap_fill', 'micro_pullback', 'double_bottom_top',
  ];

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-lg font-semibold mb-3">Backtesting</h2>

      {/* Config form */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div>
          <label className="text-xs text-gray-400">Start Date</label>
          <input
            type="date"
            value={startDate}
            onChange={e => setStartDate(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-gray-400">End Date</label>
          <input
            type="date"
            value={endDate}
            onChange={e => setEndDate(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-gray-400">Capital</label>
          <input
            type="number"
            value={capital}
            onChange={e => setCapital(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm"
          />
        </div>
        <div className="flex items-end">
          <button
            onClick={handleRun}
            disabled={loading || !startDate || !endDate}
            className="w-full py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 rounded font-medium text-sm transition"
          >
            {loading ? 'Running...' : 'Run Backtest'}
          </button>
        </div>
      </div>

      {/* Strategy select */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1">
          <label className="text-xs text-gray-400">Strategy</label>
          <select
            value={strategy}
            onChange={e => setStrategy(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm"
          >
            {allStrategies.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-1 text-xs text-gray-400 mt-4">
          <input
            type="checkbox"
            checked={useRegime}
            onChange={e => setUseRegime(e.target.checked)}
            className="rounded"
          />
          Regime Filter
        </label>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {/* Results */}
      {current && (
        <div className="mb-4">
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2 text-sm mb-3">
            <Stat label="Return" value={`${current.total_return_pct?.toFixed(2) ?? '--'}%`}
              color={current.total_return_pct && current.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'} />
            <Stat label="Win Rate" value={`${((current.win_rate ?? 0) * 100).toFixed(1)}%`} />
            <Stat label="Trades" value={String(current.total_trades ?? 0)} />
            <Stat label="Sharpe" value={current.sharpe_ratio?.toFixed(2) ?? '--'} />
            <Stat label="Max DD" value={`${current.max_drawdown_pct?.toFixed(2) ?? '--'}%`} color="text-red-400" />
            <Stat label="Profit Factor" value={current.profit_factor?.toFixed(2) ?? '--'} />
            <Stat label="Avg Win" value={`$${current.avg_win?.toFixed(2) ?? '--'}`} color="text-green-400" />
            <Stat label="Avg Loss" value={`$${current.avg_loss?.toFixed(2) ?? '--'}`} color="text-red-400" />
          </div>

          {current.equity_curve && <Chart equityCurve={current.equity_curve} />}
        </div>
      )}

      {/* History */}
      {results.length > 1 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-400 mb-2">Previous Runs</h3>
          <div className="overflow-x-auto max-h-40 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500">
                <tr>
                  <th className="px-2 py-1 text-left">Date</th>
                  <th className="px-2 py-1 text-left">Strategies</th>
                  <th className="px-2 py-1 text-left">Return</th>
                  <th className="px-2 py-1 text-left">Win Rate</th>
                  <th className="px-2 py-1 text-left">Trades</th>
                </tr>
              </thead>
              <tbody>
                {results.slice(1).map(r => (
                  <tr key={r.id} className="border-t border-gray-800/50">
                    <td className="px-2 py-1">{new Date(r.created_at).toLocaleDateString()}</td>
                    <td className="px-2 py-1">{r.strategies}</td>
                    <td className={`px-2 py-1 ${(r.total_return_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {r.total_return_pct?.toFixed(2)}%
                    </td>
                    <td className="px-2 py-1">{((r.win_rate ?? 0) * 100).toFixed(1)}%</td>
                    <td className="px-2 py-1">{r.total_trades}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className={`font-mono font-medium ${color ?? ''}`}>{value}</p>
    </div>
  );
}
