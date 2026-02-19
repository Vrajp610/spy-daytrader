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
    <div className="card p-4">
      <h2 className="card-title mb-3">Backtesting</h2>

      {/* Config form */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div>
          <label className="label mb-1 block">Start Date</label>
          <input
            type="date"
            value={startDate}
            onChange={e => setStartDate(e.target.value)}
            className="input"
          />
        </div>
        <div>
          <label className="label mb-1 block">End Date</label>
          <input
            type="date"
            value={endDate}
            onChange={e => setEndDate(e.target.value)}
            className="input"
          />
        </div>
        <div>
          <label className="label mb-1 block">Capital</label>
          <input
            type="number"
            value={capital}
            onChange={e => setCapital(Number(e.target.value))}
            className="input"
          />
        </div>
        <div className="flex items-end">
          <button
            onClick={handleRun}
            disabled={loading || !startDate || !endDate}
            className="btn-primary w-full"
          >
            {loading ? 'Running...' : 'Run Backtest'}
          </button>
        </div>
      </div>

      {/* Strategy select */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1">
          <label className="label mb-1 block">Strategy</label>
          <select
            value={strategy}
            onChange={e => setStrategy(e.target.value)}
            className="select"
          >
            {allStrategies.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted mt-5 cursor-pointer">
          <input
            type="checkbox"
            checked={useRegime}
            onChange={e => setUseRegime(e.target.checked)}
            className="rounded border-terminal-600/40 bg-terminal-900/80 text-accent focus:ring-accent/30"
          />
          Regime Filter
        </label>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 bg-loss/10 border border-loss/20 rounded-md text-loss text-sm animate-fade-in">
          {error}
        </div>
      )}

      {/* Results */}
      {current && (
        <div className="mb-4">
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2 text-sm mb-3">
            <Stat label="Return" value={`${current.total_return_pct?.toFixed(2) ?? '--'}%`}
              color={current.total_return_pct && current.total_return_pct >= 0 ? 'text-profit' : 'text-loss'} />
            <Stat label="Win Rate" value={`${((current.win_rate ?? 0) * 100).toFixed(1)}%`} />
            <Stat label="Trades" value={String(current.total_trades ?? 0)} />
            <Stat label="Sharpe" value={current.sharpe_ratio?.toFixed(2) ?? '--'} />
            <Stat label="Max DD" value={`${current.max_drawdown_pct?.toFixed(2) ?? '--'}%`} color="text-loss" />
            <Stat label="Profit Factor" value={current.profit_factor?.toFixed(2) ?? '--'} />
            <Stat label="Avg Win" value={`$${current.avg_win?.toFixed(2) ?? '--'}`} color="text-profit" />
            <Stat label="Avg Loss" value={`$${current.avg_loss?.toFixed(2) ?? '--'}`} color="text-loss" />
          </div>

          {current.equity_curve && <Chart equityCurve={current.equity_curve} />}
        </div>
      )}

      {/* History */}
      {results.length > 1 && (
        <div>
          <h3 className="label mb-2">Previous Runs</h3>
          <div className="overflow-x-auto max-h-40 overflow-y-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Strategies</th>
                  <th>Return</th>
                  <th>Win Rate</th>
                  <th>Trades</th>
                </tr>
              </thead>
              <tbody>
                {results.slice(1).map(r => (
                  <tr key={r.id}>
                    <td className="text-muted">{new Date(r.created_at).toLocaleDateString()}</td>
                    <td className="text-terminal-200">{r.strategies}</td>
                    <td className={`font-mono tabular-nums ${(r.total_return_pct ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {r.total_return_pct?.toFixed(2)}%
                    </td>
                    <td className="font-mono tabular-nums">{((r.win_rate ?? 0) * 100).toFixed(1)}%</td>
                    <td className="font-mono tabular-nums text-muted">{r.total_trades}</td>
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
    <div className="bg-terminal-700/30 rounded-md p-2">
      <p className="label">{label}</p>
      <p className={`font-mono tabular-nums font-medium ${color ?? 'text-terminal-100'}`}>{value}</p>
    </div>
  );
}
