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
    const interval = setInterval(refresh, 3000);
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

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Bot Controls</h2>
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${running ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
          <span className="text-sm">{running ? 'Running' : 'Stopped'}</span>
        </div>
      </div>

      <div className="flex gap-2 mb-3">
        <button
          onClick={handleStart}
          disabled={running || loading}
          className="flex-1 py-2 px-4 bg-green-600 hover:bg-green-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition"
        >
          Start
        </button>
        <button
          onClick={handleStop}
          disabled={!running || loading}
          className="flex-1 py-2 px-4 bg-red-600 hover:bg-red-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition"
        >
          Stop
        </button>
      </div>

      <div className="flex items-center justify-between text-sm">
        <div>
          <span className="text-gray-400">Mode: </span>
          <button
            onClick={handleModeToggle}
            className={`font-medium px-2 py-0.5 rounded ${
              mode === 'paper' ? 'bg-blue-900 text-blue-300' : 'bg-red-900 text-red-300'
            }`}
          >
            {mode.toUpperCase()}
          </button>
        </div>
        <div>
          <span className="text-gray-400">Regime: </span>
          <span className="font-mono">{regime}</span>
        </div>
      </div>
    </div>
  );
}
