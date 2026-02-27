import { useState, useEffect, useCallback } from 'react';
import { getPortfolioAnalytics, getMonteCarlo } from '../services/api';
import type { PortfolioAnalyticsData, MonteCarloResult, RollingStrategyPerf } from '../types';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt2(v: number) { return v.toFixed(2); }
function fmt4(v: number) { return v.toFixed(4); }
function pct1(v: number) { return `${(v * 100).toFixed(1)}%`; }
function dollar(v: number) {
  return (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function posNeg(v: number, text: string, inverse = false) {
  const good = inverse ? v <= 0 : v >= 0;
  return <span className={good ? 'text-profit' : 'text-loss'}>{text}</span>;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function MetricRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-terminal-800/60 last:border-0">
      <span className="text-xxs text-muted uppercase tracking-terminal" title={hint}>{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="text-xxs font-bold uppercase tracking-widest text-terminal-400 mb-2 mt-4 first:mt-0 pb-1 border-b border-terminal-700/50">
      {title}
    </div>
  );
}

function MonteCarloPanel({ mc }: { mc: MonteCarloResult }) {
  const rows = [
    { label: 'P5  (worst 5%)', v: mc.p5 },
    { label: 'P25', v: mc.p25 },
    { label: 'P50 (median)', v: mc.p50 },
    { label: 'P75', v: mc.p75 },
    { label: 'P95 (best 5%)', v: mc.p95 },
  ];
  return (
    <div>
      <div className="text-xxs text-muted mb-2">
        {mc.n_simulations.toLocaleString()} simulations × {mc.n_days}d bootstrap
      </div>
      {rows.map(r => (
        <div key={r.label} className="flex justify-between py-1 border-b border-terminal-800/40 last:border-0">
          <span className="text-xxs text-muted">{r.label}</span>
          <span className={`font-mono text-xs ${r.v >= (mc.p50) ? 'text-profit' : 'text-loss'}`}>
            ${r.v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </span>
        </div>
      ))}
      <div className="mt-2 flex gap-4">
        <div className="text-xxs">
          <span className="text-muted">Prob loss: </span>
          <span className={mc.prob_loss > 0.3 ? 'text-loss' : 'text-profit'}>{pct1(mc.prob_loss)}</span>
        </div>
        <div className="text-xxs">
          <span className="text-muted">Prob DD&gt;5%: </span>
          <span className={mc.prob_dd_5pct > 0.2 ? 'text-loss' : 'text-profit'}>{pct1(mc.prob_dd_5pct)}</span>
        </div>
      </div>
    </div>
  );
}

function RollingTable({ rolling }: { rolling: Record<string, RollingStrategyPerf> }) {
  const entries = Object.entries(rolling).sort(([, a], [, b]) => b.total_pnl - a.total_pnl);
  if (entries.length === 0) {
    return <div className="text-xxs text-muted py-4 text-center">No trades in the last 90 days.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xxs">
        <thead>
          <tr className="text-muted border-b border-terminal-700/50">
            <th className="text-left pb-1.5 font-medium uppercase tracking-terminal">Strategy</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">Trades</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">WR</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">PF</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">P&amp;L</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">CVaR95</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">Omega</th>
            <th className="text-right pb-1.5 font-medium uppercase tracking-terminal">Flag</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, m]) => (
            <tr key={name} className="border-b border-terminal-800/30 last:border-0 hover:bg-terminal-800/20">
              <td className="py-1.5 pr-2 font-mono text-terminal-300">
                {name.replace(/_/g, ' ')}
              </td>
              <td className="text-right py-1.5 font-mono">{m.trades}</td>
              <td className={`text-right py-1.5 font-mono ${m.win_rate >= 0.5 ? 'text-profit' : 'text-loss'}`}>
                {pct1(m.win_rate)}
              </td>
              <td className={`text-right py-1.5 font-mono ${m.profit_factor >= 1 ? 'text-profit' : 'text-loss'}`}>
                {m.profit_factor.toFixed(2)}
              </td>
              <td className={`text-right py-1.5 font-mono ${m.total_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {dollar(m.total_pnl)}
              </td>
              <td className="text-right py-1.5 font-mono text-loss">${m.cvar_95.toFixed(0)}</td>
              <td className={`text-right py-1.5 font-mono ${m.omega >= 1 ? 'text-profit' : 'text-loss'}`}>
                {m.omega.toFixed(2)}
              </td>
              <td className="text-right py-1.5">
                {m.insufficient_data && (
                  <span className="text-muted border border-terminal-600/40 px-1 py-0.5 text-xxs">LOW</span>
                )}
                {m.retire_recommended && !m.insufficient_data && (
                  <span className="text-loss border border-loss/40 px-1 py-0.5 text-xxs bg-loss/10">RETIRE</span>
                )}
                {!m.insufficient_data && !m.retire_recommended && (
                  <span className="text-profit border border-profit/30 px-1 py-0.5 text-xxs bg-profit/5">OK</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function PortfolioAnalytics() {
  const [data, setData] = useState<PortfolioAnalyticsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mcDays, setMcDays] = useState(21);
  const [mcLoading, setMcLoading] = useState(false);
  const [mcResult, setMcResult] = useState<MonteCarloResult | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const snap = await getPortfolioAnalytics();
      setData(snap);
      setMcResult(snap.monte_carlo);
    } catch {
      setError('Failed to load analytics. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  const rerunMonteCarlo = async () => {
    setMcLoading(true);
    try {
      const result = await getMonteCarlo(2000, mcDays);
      setMcResult(result);
    } finally {
      setMcLoading(false);
    }
  };

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="space-y-3">

      {/* Header bar */}
      <div className="card p-3 flex items-center justify-between">
        <div>
          <h2 className="card-title">Portfolio Analytics</h2>
          <p className="text-xxs text-muted mt-0.5">CVaR · Omega · Ulcer · Monte Carlo · Rolling 90d</p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="btn-ghost text-xxs px-3 py-1.5 border border-terminal-600/40"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="card p-3 text-loss text-xs border-loss/30">{error}</div>
      )}

      {data && (
        <>
          {/* Retire alerts */}
          {data.retire_recommendations.length > 0 && (
            <div className="card p-3 border-loss/40 bg-loss/5">
              <div className="text-xxs font-bold uppercase tracking-widest text-loss mb-1">
                Retirement Recommended ({data.retire_recommendations.length})
              </div>
              <div className="flex flex-wrap gap-2">
                {data.retire_recommendations.map(s => (
                  <span key={s} className="text-xxs font-mono text-loss border border-loss/30 px-1.5 py-0.5">
                    {s.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Two-column top section */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">

            {/* Risk Metrics card */}
            <div className="card p-4">
              <SectionHeader title="Risk Metrics" />
              <MetricRow
                label="CVaR 95%"
                hint="Expected loss in worst 5% of sessions"
                value={posNeg(-data.cvar_95, `$${data.cvar_95.toFixed(2)}`, true)}
              />
              <MetricRow
                label="Omega Ratio"
                hint="Probability-weighted gains / losses (> 1 = net positive)"
                value={posNeg(data.omega_ratio - 1, data.omega_ratio.toFixed(4))}
              />
              <MetricRow
                label="Ulcer Index"
                hint="RMS of % drawdowns from equity peak (lower = better)"
                value={posNeg(-data.ulcer_index, data.ulcer_index.toFixed(4), true)}
              />
              <MetricRow
                label="Sortino Ratio"
                hint="Mean return / downside deviation"
                value={posNeg(data.sortino_ratio, fmt4(data.sortino_ratio))}
              />
              <MetricRow label="Total Trades" value={<span className="text-terminal-300">{data.total_trades}</span>} />
              <MetricRow
                label="Total P&L"
                value={posNeg(data.total_pnl, dollar(data.total_pnl))}
              />
              <MetricRow
                label="Equity"
                value={<span className="text-terminal-100">${data.equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>}
              />

              <SectionHeader title="Portfolio Greeks" />
              <MetricRow label="Net Delta" value={posNeg(data.greeks.delta, fmt4(data.greeks.delta))} />
              <MetricRow label="Gamma" value={<span className="text-terminal-300">{fmt4(data.greeks.gamma)}</span>} />
              <MetricRow label="Theta ($/day)" value={posNeg(data.greeks.theta, fmt2(data.greeks.theta))} />
              <MetricRow label="Vega" value={<span className="text-terminal-300">{fmt4(data.greeks.vega)}</span>} />
              <MetricRow
                label="Delta Notional"
                hint="Dollar exposure from net delta (delta × underlying × 100)"
                value={posNeg(data.greeks.net_delta_notional, dollar(data.greeks.net_delta_notional))}
              />
              <MetricRow
                label="Delta Exposure %"
                hint="Net delta notional as % of equity"
                value={
                  <span className={data.delta_adjusted_exposure_pct > 10 ? 'text-caution' : 'text-terminal-300'}>
                    {data.delta_adjusted_exposure_pct.toFixed(2)}%
                  </span>
                }
              />
            </div>

            {/* Monte Carlo card */}
            <div className="card p-4">
              <SectionHeader title="Monte Carlo Stress Test" />
              <div className="flex items-center gap-2 mb-3">
                <label className="text-xxs text-muted uppercase tracking-terminal">Horizon</label>
                <select
                  value={mcDays}
                  onChange={e => setMcDays(Number(e.target.value))}
                  className="bg-terminal-800 border border-terminal-600/40 text-xxs text-terminal-200 px-2 py-1 font-mono"
                >
                  {[5, 10, 21, 42, 63].map(d => (
                    <option key={d} value={d}>{d}d</option>
                  ))}
                </select>
                <button
                  onClick={rerunMonteCarlo}
                  disabled={mcLoading}
                  className="btn-ghost text-xxs px-2 py-1 border border-terminal-600/40"
                >
                  {mcLoading ? '…' : 'Run'}
                </button>
              </div>
              {mcResult && <MonteCarloPanel mc={mcResult} />}
              {!mcResult && (
                <div className="text-xxs text-muted text-center py-4">
                  Not enough trade history for Monte Carlo (min 10 trades).
                </div>
              )}
            </div>
          </div>

          {/* Rolling 90d performance table */}
          <div className="card p-4">
            <SectionHeader title="Rolling 90-Day Performance by Strategy" />
            <RollingTable rolling={data.rolling_90d} />
          </div>
        </>
      )}

      {!data && !loading && !error && (
        <div className="card p-8 text-center text-muted text-xs">Click Refresh to load analytics.</div>
      )}
    </div>
  );
}
