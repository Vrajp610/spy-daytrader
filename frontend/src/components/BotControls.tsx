import { useState, useEffect } from 'react';
import { getStatus, startBot, stopBot, setMode } from '../services/api';
import type { BotStatus } from '../types';

export default function BotControls() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    try {
      setStatus(await getStatus());
    } catch { /* ignore */ }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleStart = async () => {
    setLoading(true);
    try { await startBot(); await refresh(); } finally { setLoading(false); }
  };

  const handleStop = async () => {
    setLoading(true);
    try { await stopBot(); await refresh(); } finally { setLoading(false); }
  };

  const handleModeToggle = async () => {
    if (!status) return;
    const newMode = status.mode === 'paper' ? 'live' : 'paper';
    if (newMode === 'live') {
      const confirmed = window.prompt(
        'Type "I understand the risks of live trading" to enable live mode:'
      );
      if (confirmed !== 'I understand the risks of live trading') return;
      await setMode('live', confirmed);
    } else {
      await setMode('paper');
    }
    await refresh();
  };

  const running = status?.running ?? false;
  const mode = status?.mode ?? 'paper';
  const regime = status?.current_regime ?? '--';
  const dailyPnl = status?.daily_pnl ?? 0;
  const totalPnl = status?.total_pnl ?? 0;
  const equity = status?.equity ?? 0;

  const fmtPnl = (v: number) =>
    `${v >= 0 ? '+' : ''}$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  return (
    <div className={`card p-4 ${running ? 'accent-left-green' : 'accent-left-red'}`}>
      <div className="card-header mb-3">
        <h2 className="card-title">Bot Controls</h2>
        {running ? (
          <span className="badge badge-active animate-glow-pulse">
            <span className="w-1.5 h-1.5 rounded-full bg-profit" />
            Running
          </span>
        ) : (
          <span className="badge badge-live">
            <span className="w-1.5 h-1.5 rounded-full bg-loss" />
            Stopped
          </span>
        )}
      </div>

      <div className="flex gap-2 mb-3">
        <button
          onClick={handleStart}
          disabled={running || loading}
          className="btn-success flex-1"
        >
          Start
        </button>
        <button
          onClick={handleStop}
          disabled={!running || loading}
          className="btn-danger flex-1"
        >
          Stop
        </button>
      </div>

      <div className="flex items-center justify-between grid-line pb-2">
        <span className="label">Mode</span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleModeToggle}
            className={`badge ${mode === 'paper' ? 'badge-paper' : 'badge-live'}`}
          >
            {mode.toUpperCase()}
          </button>
          <span className="badge badge-active text-xxs">OPTIONS</span>
        </div>
      </div>

      <div className="flex items-center justify-between grid-line py-2">
        <span className="label">Equity</span>
        <span className="font-mono text-xs text-terminal-100">
          ${equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      </div>

      <div className="flex items-center justify-between grid-line py-2">
        <span className="label">Daily P&L</span>
        <span className={`font-mono text-xs font-semibold ${dailyPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
          {fmtPnl(dailyPnl)}
        </span>
      </div>

      <div className="flex items-center justify-between grid-line py-2">
        <span className="label">Overall P&L</span>
        <span className={`font-mono text-xs font-semibold ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
          {fmtPnl(totalPnl)}
        </span>
      </div>

      <div className="flex items-center justify-between py-2">
        <span className="label">Regime</span>
        <span className="font-mono text-xs bg-terminal-700/50 px-2 py-0.5 rounded text-terminal-200" title={regime}>
          {regime.replace(/_/g, ' ')}
        </span>
      </div>

      {running && (
        <div className="pt-2 border-t border-terminal-600/30">
          <span className="label block mb-1">Strategy</span>
          <span className="text-xxs font-mono text-terminal-200">
            {regime === 'TRENDING_UP' && 'Put Credit Spread / Call Debit Spread'}
            {regime === 'TRENDING_DOWN' && 'Call Credit Spread / Put Debit Spread'}
            {regime === 'RANGE_BOUND' && 'Iron Condor / Credit Spreads'}
            {regime === 'VOLATILE' && 'Long Straddle / Strangle'}
            {regime === '--' && 'Waiting for data...'}
          </span>
        </div>
      )}
    </div>
  );
}
