import { useState, useEffect, useCallback, useRef } from 'react';
import Chart from './Chart';
import { runCrisisBacktest, getCrisisProgress, getCrisisResult } from '../services/api';
import type {
  CrisisBacktestResult,
  CrisisProgress,
  CrisisStrategyRanking,
  LongTermBacktestResult,
} from '../types';

const ALL_STRATEGIES = [
  'vwap_reversion', 'ema_crossover', 'mtf_momentum',
  'adx_trend', 'keltner_breakout',
  'rsi2_mean_reversion', 'smc_ict',
  'theta_decay',
  'orb_scalp', 'trend_continuation', 'zero_dte_bull_put', 'vol_spike',
];

const WINDOW_IDS = [
  'dot_com_crash', 'gfc', 'covid_crash',
  'normal_2003_2006', 'normal_2010_2019', 'normal_post_covid',
];
const WINDOW_LABELS: Record<string, string> = {
  dot_com_crash:     'Dot-com Crash',
  gfc:               'GFC',
  covid_crash:       'COVID Crash',
  normal_2003_2006:  'Post-Dot-com Bull',
  normal_2010_2019:  'Long Bull',
  normal_post_covid: 'Post-COVID Bull',
};
const CRISIS_IDS = ['dot_com_crash', 'gfc', 'covid_crash'];
const SCENARIOS = [
  { id: 'baseline',        label: 'Baseline' },
  { id: 'double_slippage', label: '2× Slippage' },
  { id: 'triple_spreads',  label: '3× Spreads' },
  { id: 'full_stress',     label: 'Full Stress' },
];

type ResultTab = 'overview' | 'per_window' | 'stress' | 'recommendations';

// ── Helpers ──────────────────────────────────────────────────────────────────

function sharepColor(v: number): string {
  if (v >= 0) return 'text-profit';
  if (v >= -0.3) return 'text-yellow-400';
  return 'text-loss';
}

function badgeStyle(badge: string): string {
  switch (badge) {
    case 'resilient':      return 'bg-profit/15 text-profit border border-profit/30';
    case 'vulnerable':     return 'bg-loss/15 text-loss border border-loss/30';
    case 'stress_sensitive': return 'bg-yellow-500/15 text-yellow-300 border border-yellow-500/30';
    default:               return 'bg-terminal-700/30 text-terminal-300 border border-terminal-600/40';
  }
}

function actionBadge(action: string): string {
  switch (action) {
    case 'KEEP':   return 'bg-profit/15 text-profit border border-profit/30';
    case 'REFINE': return 'bg-yellow-500/15 text-yellow-300 border border-yellow-500/30';
    case 'RETIRE': return 'bg-loss/15 text-loss border border-loss/30';
    default:       return 'bg-terminal-700/30 text-terminal-300';
  }
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-terminal-700/30 rounded-md p-2">
      <p className="label">{label}</p>
      <p className={`font-mono tabular-nums font-medium ${color ?? 'text-terminal-100'}`}>{value}</p>
    </div>
  );
}

// ── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab({ result }: { result: CrisisBacktestResult }) {
  const { report } = result;
  const rankings = report.strategy_rankings;

  return (
    <div>
      <p className="text-xs text-muted mb-3">
        Per-strategy performance during crises (individual baseline runs). Color: green ≥ 0, yellow −0.3–0, red &lt; −0.3.
      </p>
      <div className="overflow-x-auto">
        <table className="data-table text-xs">
          <thead>
            <tr>
              <th className="text-left">Strategy</th>
              <th>Crisis Sharpe Avg</th>
              <th>Normal Sharpe Avg</th>
              <th>Crisis MaxDD</th>
              <th>Crisis Comp.</th>
              <th>Badge</th>
            </tr>
          </thead>
          <tbody>
            {rankings.map((r: CrisisStrategyRanking) => (
              <tr key={r.strategy}>
                <td className="font-mono text-terminal-200">{r.strategy}</td>
                <td className={`font-mono tabular-nums ${sharepColor(r.crisis_sharpe)}`}>{r.crisis_sharpe.toFixed(2)}</td>
                <td className={`font-mono tabular-nums ${sharepColor(r.normal_sharpe)}`}>{r.normal_sharpe.toFixed(2)}</td>
                <td className="font-mono tabular-nums text-loss">{r.crisis_max_dd.toFixed(1)}%</td>
                <td className={`font-mono tabular-nums font-medium ${r.crisis_composite >= 0.5 ? 'text-profit' : r.crisis_composite >= 0.1 ? 'text-yellow-400' : 'text-loss'}`}>
                  {r.crisis_composite.toFixed(3)}
                </td>
                <td>
                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${badgeStyle(r.badge)}`}>
                    {r.badge}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Per-Window Tab ────────────────────────────────────────────────────────────

function PerWindowTab({ result }: { result: CrisisBacktestResult }) {
  const [selectedWindow, setSelectedWindow] = useState(WINDOW_IDS[0]);
  const [selectedScenario, setSelectedScenario] = useState('baseline');

  const windowData = result.windows[selectedWindow];
  const isCrisis   = windowData?.is_crisis ?? false;
  const scenarioData: LongTermBacktestResult | undefined =
    windowData?.scenarios?.[selectedScenario];

  return (
    <div>
      {/* Window selector */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        {WINDOW_IDS.map(wid => (
          <button
            key={wid}
            onClick={() => { setSelectedWindow(wid); setSelectedScenario('baseline'); }}
            className={`px-2 py-0.5 text-xs rounded border transition-colors ${
              selectedWindow === wid
                ? 'border-accent/60 bg-accent/10 text-accent'
                : 'border-terminal-600/40 text-terminal-400 hover:border-terminal-500/60'
            }`}
          >
            {WINDOW_LABELS[wid]}
          </button>
        ))}
      </div>

      {/* Scenario sub-tabs (crisis windows only) */}
      {isCrisis && (
        <div className="flex gap-1 mb-3 border-b border-terminal-700/40 pb-2">
          {SCENARIOS.map(s => (
            <button
              key={s.id}
              onClick={() => setSelectedScenario(s.id)}
              className={`px-2.5 py-1 text-xs rounded-t transition-colors ${
                selectedScenario === s.id
                  ? 'bg-terminal-700/60 text-terminal-100 border border-terminal-600/40'
                  : 'text-terminal-500 hover:text-terminal-300'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      {scenarioData ? (
        <>
          <div className="mb-1">
            <span className="text-xs text-muted">{windowData?.period}</span>
            {isCrisis && selectedScenario !== 'baseline' && (
              <span className="ml-2 text-xs text-yellow-400">
                Stress scenario: {SCENARIOS.find(s => s.id === selectedScenario)?.label}
              </span>
            )}
          </div>
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2 text-sm mb-3">
            <Stat label="CAGR" value={`${scenarioData.cagr_pct.toFixed(2)}%`}
              color={scenarioData.cagr_pct >= 0 ? 'text-profit' : 'text-loss'} />
            <Stat label="Sharpe" value={scenarioData.sharpe_ratio.toFixed(2)} />
            <Stat label="Sortino" value={scenarioData.sortino_ratio.toFixed(2)} />
            <Stat label="Calmar" value={scenarioData.calmar_ratio.toFixed(2)} />
            <Stat label="Max DD" value={`${scenarioData.max_drawdown_pct.toFixed(2)}%`} color="text-loss" />
            <Stat label="Win Rate" value={`${(scenarioData.win_rate * 100).toFixed(1)}%`} />
            <Stat label="Total Return" value={`${scenarioData.total_return_pct.toFixed(2)}%`}
              color={scenarioData.total_return_pct >= 0 ? 'text-profit' : 'text-loss'} />
            <Stat label="Trades" value={String(scenarioData.total_trades)} />
          </div>
          {scenarioData.equity_curve.length > 0 && (
            <Chart equityCurve={scenarioData.equity_curve} />
          )}
        </>
      ) : (
        <p className="text-muted text-sm">No data for this window/scenario.</p>
      )}
    </div>
  );
}

// ── Stress Sensitivity Tab ────────────────────────────────────────────────────

function StressTab({ result }: { result: CrisisBacktestResult }) {
  return (
    <div>
      <p className="text-xs text-muted mb-3">
        Each cell shows Sharpe / MaxDD% for crisis windows under each stress scenario. Delta from baseline in smaller text.
      </p>
      <div className="overflow-x-auto">
        <table className="data-table text-xs">
          <thead>
            <tr>
              <th className="text-left">Window</th>
              {SCENARIOS.map(s => <th key={s.id}>{s.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {CRISIS_IDS.map(wid => {
              const windowData = result.windows[wid];
              if (!windowData) return null;
              const baselineSharpe = windowData.scenarios?.['baseline']?.sharpe_ratio ?? 0;
              const baselineDD     = windowData.scenarios?.['baseline']?.max_drawdown_pct ?? 0;
              return (
                <tr key={wid}>
                  <td className="font-mono text-terminal-200">{WINDOW_LABELS[wid]}</td>
                  {SCENARIOS.map(s => {
                    const sd = windowData.scenarios?.[s.id];
                    if (!sd) return <td key={s.id} className="text-muted">—</td>;
                    const sharpe = sd.sharpe_ratio;
                    const dd     = sd.max_drawdown_pct;
                    const dSharpe = sharpe - baselineSharpe;
                    const dDD     = dd - baselineDD;
                    return (
                      <td key={s.id} className="font-mono tabular-nums">
                        <span className={sharepColor(sharpe)}>{sharpe.toFixed(2)}</span>
                        <span className="text-muted"> / </span>
                        <span className="text-loss">{dd.toFixed(1)}%</span>
                        {s.id !== 'baseline' && (
                          <div className="text-[10px] text-muted">
                            Δ {dSharpe >= 0 ? '+' : ''}{dSharpe.toFixed(2)} / {dDD >= 0 ? '+' : ''}{dDD.toFixed(1)}%
                          </div>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Recommendations Tab ───────────────────────────────────────────────────────

function RecommendationsTab({ result }: { result: CrisisBacktestResult }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const { action_plan } = result.report;

  return (
    <div className="space-y-2">
      {action_plan.map(item => (
        <div key={item.strategy} className="border border-terminal-700/40 rounded-md overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-3 py-2 hover:bg-terminal-700/20 transition-colors text-left"
            onClick={() => setExpanded(prev => prev === item.strategy ? null : item.strategy)}
          >
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted tabular-nums">#{item.priority}</span>
              <span className="font-mono text-sm text-terminal-200">{item.strategy}</span>
              <span className={`px-1.5 py-0.5 rounded text-xs font-semibold ${actionBadge(item.action)}`}>
                {item.action}
              </span>
            </div>
            <span className="text-muted text-xs">{expanded === item.strategy ? '▲' : '▼'}</span>
          </button>
          {expanded === item.strategy && (
            <div className="px-3 py-2 border-t border-terminal-700/40 bg-terminal-800/30">
              <p className="text-xs text-terminal-300 leading-relaxed">{item.reason}</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function CrisisAnalysis() {
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>(ALL_STRATEGIES);
  const [capital, setCapital] = useState(50000);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<CrisisProgress | null>(null);
  const [result, setResult] = useState<CrisisBacktestResult | null>(null);
  const [activeTab, setActiveTab] = useState<ResultTab>('overview');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const prog = await getCrisisProgress();
        setProgress(prog);
        if (prog.status === 'done') {
          stopPolling();
          const res = await getCrisisResult();
          setResult(res);
          setLoading(false);
        } else if (prog.status === 'error') {
          stopPolling();
          setError('Crisis backtest encountered an error. Check backend logs.');
          setLoading(false);
        }
      } catch {
        // ignore transient poll errors
      }
    }, 2000);
  }, [stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const toggleStrategy = (s: string) =>
    setSelectedStrategies(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    );

  const handleRun = async () => {
    if (selectedStrategies.length === 0) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setProgress(null);
    try {
      await runCrisisBacktest({ strategies: selectedStrategies, initial_capital: capital });
      startPolling();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to start crisis backtest';
      setError(msg);
      setLoading(false);
    }
  };

  const progressPct = progress && progress.total > 0
    ? Math.round((progress.completed / progress.total) * 100)
    : 0;

  const RESULT_TABS: { id: ResultTab; label: string }[] = [
    { id: 'overview',         label: 'Overview' },
    { id: 'per_window',       label: 'Per-Window' },
    { id: 'stress',           label: 'Stress Sensitivity' },
    { id: 'recommendations',  label: 'Recommendations' },
  ];

  return (
    <>
      {/* Data note banner */}
      <div className="mb-4 px-3 py-2 bg-terminal-700/20 border border-terminal-600/30 rounded-md text-xs text-terminal-400">
        <span className="font-medium text-terminal-300">Data basis:</span> yfinance daily OHLCV bars (SPY back to 1993).
        Slippage: 1 bp/side × mult. Commission: $0.005/share × mult.
        Upgrade path: <span className="text-terminal-300">Polygon.io options add-on</span> (~$79/mo) for real options data.
      </div>

      {/* Strategy selector */}
      <div className="mb-4">
        <label className="label mb-1 block">
          Strategies ({selectedStrategies.length}/{ALL_STRATEGIES.length} selected)
        </label>
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

      {/* Capital + Run */}
      <div className="flex items-end gap-3 mb-4">
        <div>
          <label className="label mb-1 block">Capital</label>
          <input
            type="number"
            value={capital}
            onChange={e => setCapital(Number(e.target.value))}
            className="input w-36"
          />
        </div>
        <button
          onClick={handleRun}
          disabled={loading || selectedStrategies.length === 0}
          className="btn-primary"
        >
          {loading ? 'Running...' : 'Run Crisis Analysis'}
        </button>
      </div>

      {/* Progress bar */}
      {loading && progress && (
        <div className="mb-4">
          <div className="flex items-center justify-between text-xs text-muted mb-1">
            <span>
              {progress.current_window
                ? `${progress.current_window} — ${progress.current_scenario}`
                : 'Initialising…'}
            </span>
            <span>{progress.completed}/{progress.total}</span>
          </div>
          <div className="w-full bg-terminal-800 rounded-full h-1.5">
            <div
              className="bg-accent h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {progress.errors > 0 && (
            <p className="text-xs text-yellow-400 mt-1">{progress.errors} window(s) had errors (data may be limited for older dates)</p>
          )}
        </div>
      )}

      {error && (
        <div className="mb-3 px-3 py-2 bg-loss/10 border border-loss/20 rounded-md text-loss text-sm">
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Result tabs */}
          <div className="flex flex-wrap gap-1 mb-4 border-b border-terminal-700/40">
            {RESULT_TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`px-3 py-1.5 text-xs transition-colors ${
                  activeTab === t.id
                    ? 'text-accent border-b-2 border-accent'
                    : 'text-terminal-400 hover:text-terminal-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {activeTab === 'overview'        && <OverviewTab result={result} />}
          {activeTab === 'per_window'      && <PerWindowTab result={result} />}
          {activeTab === 'stress'          && <StressTab result={result} />}
          {activeTab === 'recommendations' && <RecommendationsTab result={result} />}

          <p className="mt-4 text-[10px] text-terminal-600">{result.data_note}</p>
        </>
      )}
    </>
  );
}
