import { useState, useEffect, useRef } from 'react';
import type { WSMessage } from '../types';

interface Alert {
  id: number;
  action: 'OPEN' | 'CLOSE';
  strategy: string;
  option_strategy_type: string;
  contracts: number;
  net_premium: number;
  display: string;
  max_loss?: number;
  max_profit?: number;
  pnl?: number;
  regime: string;
  timestamp: number;
}

interface Props {
  lastMessage: WSMessage | null;
}

const MAX_ALERTS = 50;

const TYPE_ABBREV: Record<string, string> = {
  LONG_CALL: 'LC',
  LONG_PUT: 'LP',
  CALL_DEBIT_SPREAD: 'CDS',
  CALL_CREDIT_SPREAD: 'CCS',
  PUT_DEBIT_SPREAD: 'PDS',
  PUT_CREDIT_SPREAD: 'PCS',
  IRON_CONDOR: 'IC',
  LONG_STRADDLE: 'STR',
  LONG_STRANGLE: 'STRG',
};

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function AlertsFeed({ lastMessage }: Props) {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const processedRef = useRef<number>(0);

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'trade_update') return;

    const data = lastMessage.data;
    const now = Date.now();

    // Avoid processing the same message object reference twice
    if (processedRef.current === now) return;
    processedRef.current = now;

    const alert: Alert = {
      id: now,
      action: (data.action as string)?.toUpperCase() === 'CLOSE' ? 'CLOSE' : 'OPEN',
      strategy: (data.strategy as string) ?? 'Unknown',
      option_strategy_type: (data.option_strategy_type as string) ?? '',
      contracts: (data.contracts as number) ?? (data.quantity as number) ?? 0,
      net_premium: (data.net_premium as number) ?? 0,
      display: (data.display as string) ?? '',
      max_loss: data.max_loss as number | undefined,
      max_profit: data.max_profit as number | undefined,
      pnl: data.pnl as number | undefined,
      regime: (data.regime as string) ?? (data.current_regime as string) ?? '--',
      timestamp: now,
    };

    setAlerts(prev => [alert, ...prev].slice(0, MAX_ALERTS));
  }, [lastMessage]);

  const borderColor = (alert: Alert): string => {
    if (alert.action === 'OPEN') return 'border-l-profit';
    // CLOSE: color by pnl
    if (alert.pnl != null && alert.pnl >= 0) return 'border-l-profit';
    return 'border-l-loss';
  };

  const actionBadge = (alert: Alert) => {
    if (alert.action === 'OPEN') {
      return <span className="badge badge-active text-xxs px-1.5">OPEN</span>;
    }
    const isProfit = alert.pnl != null && alert.pnl >= 0;
    return (
      <span className={`badge text-xxs px-1.5 ${isProfit ? 'badge-active' : 'badge-live'}`}>
        CLOSE
      </span>
    );
  };

  return (
    <div className="card p-4">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <h2 className="card-title">Alerts Feed</h2>
          <span className="badge badge-paper">{alerts.length}</span>
        </div>
        {alerts.length > 0 && (
          <button
            onClick={() => setAlerts([])}
            className="text-muted hover:text-terminal-200 text-xxs transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      <div className="overflow-y-auto max-h-48 space-y-1">
        {alerts.length === 0 ? (
          <div className="text-center text-muted text-xs py-6">No alerts yet</div>
        ) : (
          alerts.map(alert => (
            <div
              key={alert.id}
              className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-terminal-700/20 border-l-2 ${borderColor(alert)} animate-fade-in`}
            >
              {/* Time */}
              <span className="font-mono text-xxs tabular-nums text-muted shrink-0">
                {formatTime(alert.timestamp)}
              </span>

              {/* Action badge */}
              <span className="shrink-0">{actionBadge(alert)}</span>

              {/* Strategy */}
              <span className="text-xxs text-terminal-200 truncate shrink-0">
                {alert.strategy}
              </span>

              {/* Strategy type abbreviation */}
              {alert.option_strategy_type && (
                <span className="badge badge-paper text-xxs px-1.5 shrink-0">
                  {TYPE_ABBREV[alert.option_strategy_type] || alert.option_strategy_type}
                </span>
              )}

              {/* Contracts */}
              <span className="font-mono text-xxs tabular-nums text-muted shrink-0">
                x{alert.contracts}
              </span>

              {/* Premium */}
              <span className="font-mono text-xxs tabular-nums text-terminal-200 shrink-0">
                ${Math.abs(alert.net_premium).toFixed(2)}
                <span className="text-subtle ml-0.5">
                  {alert.net_premium < 0 ? 'cr' : 'db'}
                </span>
              </span>

              {/* P&L for CLOSE */}
              {alert.action === 'CLOSE' && alert.pnl != null && (
                <span className={`font-mono text-xxs tabular-nums font-medium shrink-0 ${alert.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {alert.pnl >= 0 ? '+' : ''}${alert.pnl.toFixed(2)}
                </span>
              )}

              {/* Display string */}
              {alert.display && (
                <span className="text-xxs text-muted truncate ml-auto" title={alert.display}>
                  {alert.display}
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
