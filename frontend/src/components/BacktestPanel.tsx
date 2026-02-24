import { useState, useEffect } from 'react';
import { useBacktest } from '../hooks/useBacktest';
import Chart from './Chart';
import { runLongTermBacktest } from '../services/api';
import type { LongTermBacktestResult } from '../types';

type Tab = 'short' | 'long';

const ALL_STRATEGIES = [
  'vwap_reversion', 'orb', 'ema_crossover', 'volume_flow', 'mtf_momentum',
  'rsi_divergence', 'bb_squeeze', 'macd_reversal', 'momentum_scalper',
  'gap_fill', 'micro_pullback', 'double_bottom_top',
];

// ── Date preset helpers ───────────────────────────────────────────────────────

function today(): string {
  return new Date().toISOString().split('T')[0];
}

function yearsAgo(n: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - n);
  return d.toISOString().split('T')[0];
}

const PRESETS = [
  { label: '1Y',  start: () => yearsAgo(1) },
  { label: '3Y',  start: () => yearsAgo(3) },
  { label: '5Y',  start: () => yearsAgo(5) },
  { label: '10Y', start: () => yearsAgo(10) },
  { label: '15Y', start: () => yearsAgo(15) },
  { label: 'MAX', start: () => '2007-01-01' },
];

// ── Sub-components ────────────────────────────────────────────────────────────

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-terminal-700/30 rounded-md p-2">
      <p className="label">{label}</p>
      <p className={`font-mono tabular-nums font-medium ${color ?? 'text-terminal-100'}`}>{value}</p>
    </div>
  );
}

// ── Short-term tab (unchanged) ────────────────────────────────────────────────

function ShortTermTab() {
  const { results, current, loading, error, run, loadHistory } = useBacktest();
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [strategy, setStrategy] = useState('vwap_reversion');
  const [useRegime, setUseRegime] = useState(true);
  const [capital, setCapital] = useState(25000);

  useEffect(() => {
    loadHistory();
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

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div>
          <label className="label mb-1 block">Start Date</label>
          <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="input" />
        </div>
        <div>
          <label className="label mb-1 block">End Date</label>
          <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="input" />
        </div>
        <div>
          <label className="label mb-1 block">Capital</label>
          <input type="number" value={capital} onChange={e => setCapital(Number(e.target.value))} className="input" />
        </div>
        <div className="flex items-end">
          <button onClick={handleRun} disabled={loading || !startDate || !endDate} className="btn-primary w-full">
            {loading ? 'Running...' : 'Run Backtest'}
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1">
          <label className="label mb-1 block">Strategy</label>
          <select value={strategy} onChange={e => setStrategy(e.target.value)} className="select">
            {ALL_STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
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

      {results.length > 1 && (
        <div>
          <h3 className="label mb-2">Previous Runs</h3>
          <div className="overflow-x-auto max-h-40 overflow-y-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th><th>Strategies</th><th>Return</th><th>Win Rate</th><th>Trades</th>
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
    </>
  );
}

// ── Long-term tab ─────────────────────────────────────────────────────────────

function LongTermTab() {
  const [startDate, setStartDate] = useState(yearsAgo(10));
  const [endDate, setEndDate] = useState(today());
  const [capital, setCapital] = useState(25000);
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>(ALL_STRATEGIES);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LongTermBacktestResult | null>(null);

  const applyPreset = (start: string) => {
    setStartDate(start);
    setEndDate(today());
  };

  const toggleStrategy = (s: string) => {
    setSelectedStrategies(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    );
  };

  const handleRun = async () => {
    if (!startDate || !endDate || selectedStrategies.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const res = await runLongTermBacktest({
        start_date: startDate,
        end_date: endDate,
        initial_capital: capital,
        strategies: selectedStrategies,
      });
      setResult(res);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Long-term backtest failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {/* Presets */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="label">Preset:</span>
        {PRESETS.map(p => (
          <button
            key={p.label}
            onClick={() => applyPreset(p.start())}
            className="px-2 py-0.5 text-xs rounded border border-terminal-600/40 text-terminal-300 hover:bg-terminal-700/50 transition-colors"
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Date + Capital */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div>
          <label className="label mb-1 block">Start Date</label>
          <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="input" />
        </div>
        <div>
          <label className="label mb-1 block">End Date</label>
          <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="input" />
        </div>
        <div>
          <label className="label mb-1 block">Capital</label>
          <input type="number" value={capital} onChange={e => setCapital(Number(e.target.value))} className="input" />
        </div>
        <div className="flex items-end">
          <button
            onClick={handleRun}
            disabled={loading || !startDate || !endDate || selectedStrategies.length === 0}
            className="btn-primary w-full"
          >
            {loading ? 'Running...' : 'Run Long-Term'}
          </button>
        </div>
      </div>

      {/* Strategy multi-select */}
      <div className="mb-4">
        <label className="label mb-1 block">Strategies ({selectedStrategies.length}/{ALL_STRATEGIES.length} selected)</label>
        <div className="flex flex-wrap gap-1.5">
          {ALL_STRATEGIES.map(s => (
            <button
              key={s}
              onClick={() => toggleStrategy(s)}
              className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                selectedStrategies.includes(s)
                  ? 'border-accent/60 bg-accent/10 text-accent'
                  : 'border-terminal-600/40 text-terminal-500 hover:border-terminal-500/60'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 bg-loss/10 border border-loss/20 rounded-md text-loss text-sm animate-fade-in">
          {error}
        </div>
      )}

      {result && (
        <>
          {/* Extended metrics */}
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2 text-sm mb-3">
            <Stat label="CAGR" value={`${result.cagr_pct.toFixed(2)}%`}
              color={result.cagr_pct >= 0 ? 'text-profit' : 'text-loss'} />
            <Stat label="Sharpe" value={result.sharpe_ratio.toFixed(2)} />
            <Stat label="Sortino" value={result.sortino_ratio.toFixed(2)} />
            <Stat label="Calmar" value={result.calmar_ratio.toFixed(2)} />
            <Stat label="Max DD" value={`${result.max_drawdown_pct.toFixed(2)}%`} color="text-loss" />
            <Stat label="Win Rate" value={`${(result.win_rate * 100).toFixed(1)}%`} />
            <Stat label="Total Return" value={`${result.total_return_pct.toFixed(2)}%`}
              color={result.total_return_pct >= 0 ? 'text-profit' : 'text-loss'} />
            <Stat label="Trades" value={String(result.total_trades)} />
          </div>

          {/* Secondary metrics row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm mb-4">
            <Stat label="Profit Factor" value={result.profit_factor.toFixed(2)} />
            <Stat label="Avg Win" value={`$${result.avg_win.toFixed(2)}`} color="text-profit" />
            <Stat label="Avg Loss" value={`$${result.avg_loss.toFixed(2)}`} color="text-loss" />
            <Stat label="Final Capital" value={`$${result.final_capital.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
          </div>

          {/* Equity curve */}
          {result.equity_curve.length > 0 && (
            <div className="mb-4">
              <Chart equityCurve={result.equity_curve} />
            </div>
          )}

          {/* Yearly returns table */}
          {result.yearly_returns.length > 0 && (
            <div>
              <h3 className="label mb-2">Yearly Performance ({result.years_tested.toFixed(1)} years tested)</h3>
              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Year</th>
                      <th>Return %</th>
                      <th>Trades</th>
                      <th>End Equity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.yearly_returns.map(yr => (
                      <tr key={yr.year}>
                        <td className="font-mono text-terminal-200">{yr.year}</td>
                        <td className={`font-mono tabular-nums font-medium ${yr.return_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                          {yr.return_pct >= 0 ? '+' : ''}{yr.return_pct.toFixed(2)}%
                        </td>
                        <td className="font-mono tabular-nums text-muted">{yr.trades}</td>
                        <td className="font-mono tabular-nums text-terminal-200">
                          ${yr.end_equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function BacktestPanel() {
  const [tab, setTab] = useState<Tab>('short');

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="card-title">Backtesting</h2>
        <div className="flex rounded-md border border-terminal-600/40 overflow-hidden">
          <button
            onClick={() => setTab('short')}
            className={`px-3 py-1 text-xs transition-colors ${
              tab === 'short'
                ? 'bg-accent/20 text-accent border-r border-terminal-600/40'
                : 'text-terminal-400 hover:bg-terminal-700/40 border-r border-terminal-600/40'
            }`}
          >
            Short-Term
          </button>
          <button
            onClick={() => setTab('long')}
            className={`px-3 py-1 text-xs transition-colors ${
              tab === 'long'
                ? 'bg-accent/20 text-accent'
                : 'text-terminal-400 hover:bg-terminal-700/40'
            }`}
          >
            Long-Term (10–15Y)
          </button>
        </div>
      </div>

      {tab === 'short' ? <ShortTermTab /> : <LongTermTab />}
    </div>
  );
}
