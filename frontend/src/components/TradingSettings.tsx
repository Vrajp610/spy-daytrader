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
  {
    key: 'min_signal_confidence',
    label: 'Min Signal Confidence',
    description: 'Minimum confidence score (0-1) to execute a signal',
    type: 'percent',
    min: 0,
    max: 100,
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
      <div className="card p-4">
        <h2 className="card-title mb-3">Trading Settings</h2>
        <p className="text-muted text-sm">Loading...</p>
      </div>
    );
  }

  return (
    <div className="card p-4">
      <div className="card-header">
        <h2 className="card-title">Trading Settings</h2>
        <span className="badge badge-paper">Paper Mode</span>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 bg-loss/10 border border-loss/20 rounded-md text-loss text-sm animate-fade-in">
          {error}
        </div>
      )}

      {success && (
        <div className="mb-3 px-3 py-2 bg-profit/10 border border-profit/20 rounded-md text-profit text-sm animate-fade-in">
          Settings saved
        </div>
      )}

      <div className="space-y-0">
        {FIELDS.map((field, i) => {
          const isModified = editing[field.key] !== undefined;
          return (
            <div key={field.key} className={`grid grid-cols-2 gap-2 items-center py-2.5 ${i < FIELDS.length - 1 ? 'grid-line' : ''}`}>
              <div>
                <label className="text-sm font-medium text-terminal-200">{field.label}</label>
                <p className="text-xxs text-subtle">{field.description}</p>
              </div>
              <div className="relative">
                {field.type === 'currency' && (
                  <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted font-mono text-xs">$</span>
                )}
                <input
                  type="number"
                  value={getDisplayValue(field.key)}
                  onChange={e => handleChange(field.key, e.target.value)}
                  min={field.type === 'percent' ? field.min : field.min}
                  max={field.type === 'percent' ? field.max : field.max}
                  step={field.step}
                  className={`input text-right ${
                    isModified ? 'border-accent/60' : ''
                  } ${field.type === 'currency' ? 'pl-6' : ''}`}
                />
                {field.type === 'percent' && (
                  <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted font-mono text-xs">%</span>
                )}
                {field.type === 'integer' && field.key === 'cooldown_minutes' && (
                  <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted font-mono text-xs">min</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {hasChanges && (
        <div className="flex gap-2 mt-4 pt-3 border-t border-terminal-600/30 animate-fade-in">
          <button
            onClick={handleSave}
            disabled={saving}
            className="btn-primary flex-1"
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
          <button
            onClick={handleReset}
            className="btn-ghost px-4"
          >
            Reset
          </button>
        </div>
      )}
    </div>
  );
}
