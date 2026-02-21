import { useState, useEffect, useCallback } from 'react';
import { useLeaderboard } from '../hooks/useLeaderboard';
import { triggerBacktests, triggerLongTermBacktest, getLivePerformance } from '../services/api';
import type { StrategyLiveStats } from '../types';

const STRATEGY_LABELS: Record<string, string> = {
  vwap_reversion:   'VWAP Reversion',
  orb:              'ORB',
  ema_crossover:    'EMA Crossover',
  volume_flow:      'Volume Flow',
  mtf_momentum:     'MTF Momentum',
  rsi_divergence:   'RSI Divergence',
  bb_squeeze:       'BB Squeeze',
  macd_reversal:    'MACD Reversal',
  momentum_scalper: 'Mom. Scalper',
  gap_fill:         'Gap Fill',
  micro_pullback:   'Micro Pullback',
  double_bottom_top:'Dbl Bot/Top',
};

const RANK_COLORS = ['text-caution', 'text-terminal-300', 'text-orange-400'];

function fmt1(v: number)  { return v.toFixed(1); }
function fmt2(v: number)  { return v.toFixed(2); }
function pct1(v: number)  { return `${(v * 100).toFixed(1)}%`; }
function sign(v: number)  { return v >= 0 ? `+${v.toFixed(1)}` : v.toFixed(1); }

function ScorePill({ st, lt, blended }: { st: number; lt: number | null; blended: number }) {
  const hasLt = lt !== null;
  return (
    <span title={hasLt ? `ST ${st.toFixed(1)} + LT ${lt!.toFixed(1)} → blended ${blended.toFixed(1)}` : `ST only: ${st.toFixed(1)}`}>
      <span className={`font-mono tabular-nums font-semibold ${blended >= 0 ? 'text-profit' : 'text-loss'}`}>
        {blended.toFixed(1)}
      </span>
      {hasLt && (
        <span className="ml-1 text-xxs text-accent opacity-70">★</span>
      )}
    </span>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export default function StrategyLeaderboard() {
  const { rankings, progress, ltProgress, loading, refresh } = useLeaderboard();
  const [liveStats, setLiveStats] = useState<Record<string, StrategyLiveStats>>({});
  const [showLive, setShowLive] = useState(true);
  const [showLt, setShowLt] = useState(true);
  const [ltRunning, setLtRunning] = useState(false);

  const isStRunning = progress.status === 'running';
  const isLtRunning = ltProgress.status === 'running' || ltRunning;
  const stPct  = progress.total  > 0 ? Math.round((progress.completed  / progress.total)  * 100) : 0;
  const ltPct  = ltProgress.total > 0 ? Math.round((ltProgress.completed / ltProgress.total) * 100) : 0;
  const hasLtData = rankings.some(r => r.lt_composite_score !== null);
  const hasAnyLive = Object.values(liveStats).some(s => s.live_trades > 0);

  const loadLive = useCallback(async () => {
    try {
      const data = await getLivePerformance();
      const map: Record<string, StrategyLiveStats> = {};
      for (const s of data) map[s.strategy_name] = s;
      setLiveStats(map);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    loadLive();
    const id = setInterval(loadLive, 30_000);
    return () => clearInterval(id);
  }, [loadLive]);

  // Poll faster while LT is running
  useEffect(() => {
    if (!isLtRunning) return;
    const id = setInterval(refresh, 5_000);
    return () => clearInterval(id);
  }, [isLtRunning, refresh]);

  const handleStTrigger = async () => {
    await triggerBacktests();
    setTimeout(refresh, 2000);
  };

  const handleLtTrigger = async () => {
    setLtRunning(true);
    try {
      await triggerLongTermBacktest('2010-01-01');
      setTimeout(refresh, 2000);
    } finally {
      // Will be reset when ltProgress.status transitions
      setTimeout(() => setLtRunning(false), 3000);
    }
  };

  return (
    <div className="card p-4">
      {/* Header */}
      <div className="card-header mb-3">
        <h2 className="card-title">Strategy Leaderboard</h2>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Toggle columns */}
          {hasLtData && (
            <button
              onClick={() => setShowLt(v => !v)}
              className={`px-2 py-1 text-xxs rounded border transition-colors ${
                showLt
                  ? 'border-accent/60 bg-accent/10 text-accent'
                  : 'border-terminal-600/40 text-terminal-500'
              }`}
            >
              LT cols
            </button>
          )}
          {hasAnyLive && (
            <button
              onClick={() => setShowLive(v => !v)}
              className={`px-2 py-1 text-xxs rounded border transition-colors ${
                showLive
                  ? 'border-profit/60 bg-profit/10 text-profit'
                  : 'border-terminal-600/40 text-terminal-500'
              }`}
            >
              Live cols
            </button>
          )}
          {/* Run 15Y backtest */}
          <button
            onClick={handleLtTrigger}
            disabled={isLtRunning}
            className="btn-primary text-xs px-3 py-1.5 bg-accent/20 border border-accent/40 text-accent hover:bg-accent/30"
          >
            {isLtRunning ? `LT running… (${ltPct}%)` : 'Run 15Y Backtest'}
          </button>
          {/* Re-run short-term */}
          <button
            onClick={handleStTrigger}
            disabled={isStRunning}
            className="btn-primary text-xs px-3 py-1.5"
          >
            {isStRunning ? 'Running…' : 'Re-run ST'}
          </button>
        </div>
      </div>

      {/* LT progress bar */}
      {isLtRunning && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xxs text-accent">
              15Y Backtest: {ltProgress.current_test || 'initializing…'}
            </span>
            <span className="text-xxs font-mono text-muted">
              {ltProgress.completed}/{ltProgress.total} ({ltPct}%)
            </span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill bg-gradient-to-r from-accent-dim to-accent"
              style={{ width: `${ltPct}%` }}
            />
          </div>
        </div>
      )}

      {/* ST progress bar */}
      {isStRunning && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xxs text-muted">{progress.current_test}</span>
            <span className="text-xxs font-mono text-muted">{progress.completed}/{progress.total} ({stPct}%)</span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill bg-gradient-to-r from-accent-dim to-accent"
              style={{ width: `${stPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Last-run timestamps */}
      <div className="flex flex-wrap gap-4 mb-3">
        {progress.last_run && (
          <p className="text-xxs text-subtle">
            ST last run: {new Date(progress.last_run).toLocaleString()}
            {progress.errors > 0 && ` (${progress.errors} errors)`}
          </p>
        )}
        {ltProgress.last_run && (
          <p className="text-xxs text-accent/70">
            ★ LT last run: {new Date(ltProgress.last_run).toLocaleString()}
            {' '}({ltProgress.start_date} → {ltProgress.end_date})
            {ltProgress.errors > 0 && ` (${ltProgress.errors} errors)`}
          </p>
        )}
      </div>

      {/* Blended score legend */}
      {hasLtData && (
        <p className="text-xxs text-muted mb-3">
          ★ Score = 55% short-term + 45% long-term (15Y daily).
          Hover score for breakdown. Trading engine uses this blended score to weight strategy selection.
        </p>
      )}

      {/* Rankings table */}
      {rankings.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th>#</th>
                <th>Strategy</th>
                <th className="text-right">Score</th>
                {/* Short-term columns */}
                <th className="text-right">ST Sharpe</th>
                <th className="text-right">ST PF</th>
                <th className="text-right">ST Win%</th>
                <th className="text-right">ST Ret%</th>
                {/* Long-term columns */}
                {showLt && hasLtData && (
                  <>
                    <th className="text-right border-l border-accent/20">LT CAGR</th>
                    <th className="text-right">LT Sharpe</th>
                    <th className="text-right">LT Sortino</th>
                    <th className="text-right">LT WR%</th>
                    <th className="text-right">LT MaxDD</th>
                  </>
                )}
                {/* Live columns */}
                {showLive && hasAnyLive && (
                  <>
                    <th className="text-right border-l border-profit/20">Live WR%</th>
                    <th className="text-right">Live PF</th>
                    <th className="text-right">Trades</th>
                  </>
                )}
                <th className="text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {rankings.map((r, i) => {
                const live = liveStats[r.strategy_name] ?? null;
                const isDisabled  = live?.auto_disabled ?? false;
                const hasLiveTrades = (live?.live_trades ?? 0) > 0;
                const hasThisLt   = r.lt_composite_score !== null;

                return (
                  <tr key={r.strategy_name} className={isDisabled ? 'opacity-40' : undefined}>
                    <td className={`font-mono font-semibold ${RANK_COLORS[i] ?? 'text-muted'}`}>{i + 1}</td>
                    <td className="font-medium text-terminal-200 whitespace-nowrap">
                      {STRATEGY_LABELS[r.strategy_name] ?? r.strategy_name}
                    </td>

                    {/* Blended score */}
                    <td className="text-right">
                      <ScorePill
                        st={r.st_composite_score}
                        lt={r.lt_composite_score}
                        blended={r.composite_score}
                      />
                    </td>

                    {/* Short-term */}
                    <td className={`text-right font-mono tabular-nums ${r.avg_sharpe_ratio >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {fmt2(r.avg_sharpe_ratio)}
                    </td>
                    <td className={`text-right font-mono tabular-nums ${r.avg_profit_factor >= 1 ? 'text-profit' : 'text-loss'}`}>
                      {fmt2(r.avg_profit_factor)}
                    </td>
                    <td className={`text-right font-mono tabular-nums ${r.avg_win_rate >= 0.5 ? 'text-profit' : 'text-loss'}`}>
                      {pct1(r.avg_win_rate)}
                    </td>
                    <td className={`text-right font-mono tabular-nums ${r.avg_return_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {sign(r.avg_return_pct)}%
                    </td>

                    {/* Long-term columns */}
                    {showLt && hasLtData && (
                      hasThisLt ? (
                        <>
                          <td className={`text-right font-mono tabular-nums border-l border-accent/10 ${r.lt_cagr_pct! >= 0 ? 'text-profit' : 'text-loss'}`}>
                            {sign(r.lt_cagr_pct!)}%
                          </td>
                          <td className={`text-right font-mono tabular-nums ${r.lt_sharpe! >= 0 ? 'text-profit' : 'text-loss'}`}>
                            {fmt2(r.lt_sharpe!)}
                          </td>
                          <td className={`text-right font-mono tabular-nums ${r.lt_sortino! >= 0 ? 'text-profit' : 'text-loss'}`}>
                            {fmt2(r.lt_sortino!)}
                          </td>
                          <td className={`text-right font-mono tabular-nums ${r.lt_win_rate! >= 0.5 ? 'text-profit' : 'text-loss'}`}>
                            {pct1(r.lt_win_rate!)}
                          </td>
                          <td className="text-right font-mono tabular-nums text-loss">
                            {fmt1(r.lt_max_drawdown_pct!)}%
                          </td>
                        </>
                      ) : (
                        <td colSpan={5} className="text-center text-muted border-l border-accent/10">
                          <span className="text-xxs">no LT data</span>
                        </td>
                      )
                    )}

                    {/* Live columns */}
                    {showLive && hasAnyLive && (
                      hasLiveTrades ? (
                        <>
                          <td className={`text-right font-mono tabular-nums border-l border-profit/10 ${live!.live_win_rate >= r.avg_win_rate ? 'text-profit' : 'text-loss'}`}>
                            {pct1(live!.live_win_rate)}
                            <span className="ml-1 text-xxs opacity-50">
                              ({live!.live_win_rate >= r.avg_win_rate ? '+' : ''}
                              {((live!.live_win_rate - r.avg_win_rate) * 100).toFixed(1)}pp)
                            </span>
                          </td>
                          <td className={`text-right font-mono tabular-nums ${live!.live_profit_factor >= 1 ? 'text-profit' : 'text-loss'}`}>
                            {fmt2(live!.live_profit_factor)}
                          </td>
                          <td className="text-right font-mono tabular-nums text-muted">
                            {live!.live_trades}
                          </td>
                        </>
                      ) : (
                        <td colSpan={3} className="text-center text-muted border-l border-profit/10">
                          <span className="text-xxs">—</span>
                        </td>
                      )
                    )}

                    {/* Status */}
                    <td className="text-right">
                      {isDisabled ? (
                        <span
                          title={live?.disabled_reason ?? 'auto-disabled'}
                          className="inline-block px-1.5 py-0.5 text-xxs rounded bg-loss/20 text-loss border border-loss/30 cursor-help"
                        >
                          DISABLED
                        </span>
                      ) : hasLiveTrades ? (
                        <span className="inline-block px-1.5 py-0.5 text-xxs rounded bg-profit/10 text-profit border border-profit/20">
                          LIVE
                        </span>
                      ) : hasThisLt ? (
                        <span className="inline-block px-1.5 py-0.5 text-xxs rounded bg-accent/10 text-accent border border-accent/20">
                          ★ LT
                        </span>
                      ) : (
                        <span className="text-muted text-xxs">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        !loading && (
          <p className="text-sm text-muted py-6 text-center">
            {isStRunning ? 'Backtests running…' : 'No rankings yet. Backtests will run automatically.'}
          </p>
        )
      )}

      {/* Auto-disabled strategy panel */}
      {Object.values(liveStats).some(s => s.auto_disabled) && (
        <div className="mt-3 px-3 py-2 bg-loss/5 border border-loss/20 rounded-md">
          <p className="text-xxs text-loss font-medium mb-1">Auto-disabled (re-enable after 24h if backtest score ≥ 10):</p>
          {Object.values(liveStats)
            .filter(s => s.auto_disabled)
            .map(s => (
              <p key={s.strategy_name} className="text-xxs text-muted">
                <span className="text-terminal-300">{STRATEGY_LABELS[s.strategy_name] ?? s.strategy_name}</span>
                {' — '}{s.disabled_reason}
                {s.disabled_at && (
                  <span className="ml-1 opacity-60">
                    since {new Date(s.disabled_at).toLocaleTimeString()}
                  </span>
                )}
              </p>
            ))}
        </div>
      )}
    </div>
  );
}
