import { useState, useEffect } from 'react';
import { getStrategyConfigs, updateStrategyConfig } from '../services/api';
import type { StrategyConfig } from '../types';

export default function StrategyConfigPanel() {
  const [configs, setConfigs] = useState<StrategyConfig[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setConfigs(await getStrategyConfigs());
    } catch { /* ignore */ }
  };

  useEffect(() => { refresh(); }, []);

  const handleToggle = async (name: string, enabled: boolean) => {
    await updateStrategyConfig(name, { enabled });
    await refresh();
  };

  const handleParamChange = async (name: string, key: string, value: string) => {
    const config = configs.find(c => c.name === name);
    if (!config) return;
    const newParams = { ...config.params, [key]: isNaN(Number(value)) ? value : Number(value) };
    await updateStrategyConfig(name, { params: newParams });
    await refresh();
  };

  const strategyLabels: Record<string, string> = {
    vwap_reversion: 'VWAP Mean Reversion',
    orb: 'Opening Range Breakout',
    ema_crossover: 'EMA Crossover + RSI',
    volume_flow: 'Volume Profile + Order Flow',
    mtf_momentum: 'Multi-TF Momentum',
  };

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-lg font-semibold mb-3">Strategy Config</h2>

      <div className="space-y-2">
        {configs.map(c => (
          <div key={c.name} className="border border-gray-800 rounded-lg">
            <div
              className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-gray-800/50"
              onClick={() => setExpanded(expanded === c.name ? null : c.name)}
            >
              <div className="flex items-center gap-2">
                <button
                  onClick={e => { e.stopPropagation(); handleToggle(c.name, !c.enabled); }}
                  className={`w-8 h-4 rounded-full transition ${
                    c.enabled ? 'bg-green-600' : 'bg-gray-600'
                  } relative`}
                >
                  <span className={`absolute w-3 h-3 bg-white rounded-full top-0.5 transition ${
                    c.enabled ? 'left-4' : 'left-0.5'
                  }`} />
                </button>
                <span className="text-sm font-medium">{strategyLabels[c.name] ?? c.name}</span>
              </div>
              <span className="text-gray-500 text-xs">{expanded === c.name ? '\u25B2' : '\u25BC'}</span>
            </div>

            {expanded === c.name && (
              <div className="px-3 pb-3 grid grid-cols-2 gap-2">
                {Object.entries(c.params).map(([key, val]) => (
                  <div key={key}>
                    <label className="text-xs text-gray-400">{key}</label>
                    <input
                      type="text"
                      defaultValue={String(val)}
                      onBlur={e => handleParamChange(c.name, key, e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm font-mono"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
