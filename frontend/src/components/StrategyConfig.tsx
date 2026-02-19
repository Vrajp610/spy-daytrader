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
    rsi_divergence: 'RSI Divergence',
    bb_squeeze: 'Bollinger Band Squeeze',
    macd_reversal: 'MACD Reversal',
    momentum_scalper: 'Momentum Scalper',
    gap_fill: 'Gap Fill',
    micro_pullback: 'Micro Pullback',
    double_bottom_top: 'Double Bottom/Top',
  };

  return (
    <div className="card p-4">
      <h2 className="card-title mb-3">Strategy Config</h2>

      <div className="space-y-2">
        {configs.map(c => (
          <div key={c.name} className="border border-terminal-600/20 hover:border-terminal-600/40 rounded-lg transition-colors">
            <div
              className="flex items-center justify-between px-3 py-2.5 cursor-pointer"
              onClick={() => setExpanded(expanded === c.name ? null : c.name)}
            >
              <div className="flex items-center gap-2.5">
                <button
                  onClick={e => { e.stopPropagation(); handleToggle(c.name, !c.enabled); }}
                  className={`toggle ${c.enabled ? 'bg-profit' : 'bg-terminal-600'}`}
                >
                  <span className={`toggle-knob ${c.enabled ? 'left-4' : 'left-0.5'}`} />
                </button>
                <span className="text-sm font-medium text-terminal-200">{strategyLabels[c.name] ?? c.name}</span>
              </div>
              <span className={`text-subtle text-xs transition-transform duration-200 ${expanded === c.name ? 'rotate-180' : ''}`}>
                {'\u25BC'}
              </span>
            </div>

            {expanded === c.name && (
              <div className="px-3 pb-3 border-t border-terminal-600/20 pt-3 animate-fade-in">
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(c.params).map(([key, val]) => (
                    <div key={key}>
                      <label className="label mb-1 block">{key}</label>
                      <input
                        type="text"
                        defaultValue={String(val)}
                        onBlur={e => handleParamChange(c.name, key, e.target.value)}
                        className="input text-xs"
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
