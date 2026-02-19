import { useState, useEffect } from 'react';
import { getTradingSettings, updateTradingSettings } from '../services/api';
import type { TradingSettings } from '../types';

interface FieldConfig {
  key: keyof TradingSettings;
  label: string;
  description: string;
  type: 'currency' | 'percent' | 'integer';
  min: number;
  max: number;
  step: number;
}

const FIELDS: FieldConfig[] = [
  {
    key: 'initial_capital',
    label: 'Initial Capital',
    description: 'Starting account balance',
    type: 'currency',
    min: 100,
    max: 10000000,
    step: 1000,
  },
  {
    key: 'max_risk_per_trade',
    label: 'Max Risk / Trade',
    description: 'Maximum % of capital risked per trade',
    type: 'percent',
    min: 0.1,
    max: 10,
    step: 0.1,
  },
  {
    key: 'daily_loss_limit',
    label: 'Daily Loss Limit',
    description: 'Stop trading if daily loss exceeds this %',
    type: 'percent',
    min: 0.5,
    max: 20,
    step: 0.5,
  },
  {
    key: 'max_drawdown',
    label: 'Max Drawdown',
    description: 'Circuit breaker: halt if drawdown exceeds this %',
    type: 'percent',
    min: 2,
    max: 50,
    step: 1,
  },
  {
    key: 'max_position_pct',
    label: 'Max Position Size',
    description: 'Maximum % of capital in a single position',
    type: 'percent',
    min: 5,
    max: 100,
    step: 5,
  },
  {
    key: 'max_trades_per_day',
    label: 'Max Trades / Day',
    description: 'Maximum number of trades per day',
    type: 'integer',
    min: 1,
    max: 100,
    step: 1,
  },
  {
    key: 'cooldown_after_consecutive_losses',
    label: 'Cooldown After Losses',
    description: 'Number of consecutive losses before cooldown',
    type: 'integer',
    min: 1,
    max: 20,
    step: 1,
  },
  {
    key: 'cooldown_minutes',
    label: 'Cooldown Duration',
    description: 'Minutes to wait after cooldown triggers',
    type: 'integer',
    min: 1,
    max: 240,
    step: 5,
  },
];

export default function TradingSettingsPanel() {
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  const [editing, setEditing] = useState<Partial<TradingSettings>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const refresh = async () => {
    try {
      const data = await getTradingSettings();
      setSettings(data);
      setEditing({});
      setError(null);
    } catch {
      setError('Failed to load settings');
    }
  };

  useEffect(() => { refresh(); }, []);

  const handleChange = (key: keyof TradingSettings, raw: string) => {
    const field = FIELDS.find(f => f.key === key)!;
    let value: number;
    if (field.type === 'percent') {
      value = parseFloat(raw) / 100; // UI shows %, backend uses decimal
    } else if (field.type === 'integer') {
      value = parseInt(raw, 10);
    } else {
      value = parseFloat(raw);
    }
    if (!isNaN(value)) {
      setEditing(prev => ({ ...prev, [key]: value }));
    }
  };

  const getDisplayValue = (key: keyof TradingSettings): string => {
    const field = FIELDS.find(f => f.key === key)!;
    const val = editing[key] ?? settings?.[key];
    if (val === undefined || val === null) return '';
    if (field.type === 'percent') {
      return ((val as number) * 100).toFixed(1);
    }
    if (field.type === 'currency') {
      return (val as number).toFixed(0);
    }
    return String(val);
  };

  const hasChanges = Object.keys(editing).length > 0;

  const handleSave = async () => {
    if (!hasChanges) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateTradingSettings(editing);
      setSettings(updated);
      setEditing({});
      setSuccess(true);
      setTimeout(() => setSuccess(false), 2000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to save settings';
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setEditing({});
  };

  if (!settings) {
    return (
      <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
        <h2 className="text-lg font-semibold mb-3">Trading Settings</h2>
        <p className="text-gray-500 text-sm">Loading...</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Trading Settings</h2>
        <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">Paper Mode</span>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 bg-red-900/30 border border-red-800 rounded text-red-400 text-sm">
          {error}
        </div>
      )}

      {success && (
        <div className="mb-3 px-3 py-2 bg-green-900/30 border border-green-800 rounded text-green-400 text-sm">
          Settings saved
        </div>
      )}

      <div className="space-y-3">
        {FIELDS.map(field => {
          const isModified = editing[field.key] !== undefined;
          return (
            <div key={field.key} className="grid grid-cols-2 gap-2 items-center">
              <div>
                <label className="text-sm font-medium text-gray-300">{field.label}</label>
                <p className="text-xs text-gray-500">{field.description}</p>
              </div>
              <div className="relative">
                {field.type === 'currency' && (
                  <span className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500 text-sm">$</span>
                )}
                <input
                  type="number"
                  value={getDisplayValue(field.key)}
                  onChange={e => handleChange(field.key, e.target.value)}
                  min={field.type === 'percent' ? field.min : field.min}
                  max={field.type === 'percent' ? field.max : field.max}
                  step={field.step}
                  className={`w-full bg-gray-800 border rounded px-2 py-1.5 text-sm font-mono text-right ${
                    isModified ? 'border-blue-500' : 'border-gray-700'
                  } ${field.type === 'currency' ? 'pl-6' : ''}`}
                />
                {field.type === 'percent' && (
                  <span className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 text-sm">%</span>
                )}
                {field.type === 'integer' && field.key === 'cooldown_minutes' && (
                  <span className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 text-sm">min</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {hasChanges && (
        <div className="flex gap-2 mt-4 pt-3 border-t border-gray-800">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white text-sm font-medium py-2 rounded transition"
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
          <button
            onClick={handleReset}
            className="px-4 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm py-2 rounded transition"
          >
            Reset
          </button>
        </div>
      )}
    </div>
  );
}
