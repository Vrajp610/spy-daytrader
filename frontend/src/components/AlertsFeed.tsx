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
  pnl_pct?: number;
  regime: string;
  timestamp: number;
  confidence?: number;
  exit_reason?: string;
  commission?: number;
  strike?: number;
  expiration_date?: string;
  option_type?: string;
  underlying_entry?: number;
  underlying_exit?: number;
  entry_delta?: number;
  entry_iv?: number;
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
      pnl_pct: data.pnl_pct as number | undefined,
      regime: (data.regime as string) ?? (data.current_regime as string) ?? '--',
      timestamp: now,
      confidence: data.confidence as number | undefined,
      exit_reason: data.exit_reason as string | undefined,
      commission: data.commission as number | undefined,
      strike: data.strike as number | undefined,
      expiration_date: data.expiration_date as string | undefined,
      option_type: data.option_type as string | undefined,
      underlying_entry: data.underlying_entry as number | undefined,
      underlying_exit: data.underlying_exit as number | undefined,
      entry_delta: data.entry_delta as number | undefined,
      entry_iv: data.entry_iv as number | undefined,
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

      <div className="overflow-y-auto max-h-64 space-y-1">
        {alerts.length === 0 ? (
          <div className="text-center text-muted text-xs py-6">No alerts yet</div>
        ) : (
          alerts.map(alert => {
            const totalCost = Math.abs(alert.net_premium) * alert.contracts * 100;
            return (
              <div
                key={alert.id}
                className={`px-2.5 py-2 rounded-md bg-terminal-700/20 border-l-2 ${borderColor(alert)} animate-fade-in`}
              >
                {/* Line 1: Time, Action, Type, Strategy, P&L */}
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xxs tabular-nums text-muted shrink-0">
                    {formatTime(alert.timestamp)}
                  </span>
                  <span className="shrink-0">{actionBadge(alert)}</span>
                  {alert.option_strategy_type && (
                    <span className="badge badge-paper text-xxs px-1.5 shrink-0">
                      {TYPE_ABBREV[alert.option_strategy_type] || alert.option_strategy_type}
                    </span>
                  )}
                  <span className="text-xxs text-terminal-200 truncate">
                    {alert.strategy}
                  </span>
                  {alert.action === 'CLOSE' && alert.pnl != null && (
                    <span className={`font-mono text-xs tabular-nums font-semibold ml-auto shrink-0 ${alert.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {alert.pnl >= 0 ? '+' : ''}${alert.pnl.toFixed(2)}
                      {alert.pnl_pct != null && (
                        <span className="text-xxs ml-1 text-muted">({alert.pnl_pct.toFixed(1)}%)</span>
                      )}
                    </span>
                  )}
                  {alert.action === 'OPEN' && (
                    <span className="font-mono text-xxs text-terminal-300 ml-auto shrink-0">
                      ${totalCost.toFixed(0)} {alert.net_premium < 0 ? 'credit' : 'debit'}
                    </span>
                  )}
                </div>

                {/* Line 2: Contract details */}
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xxs font-mono text-terminal-400 pl-[4.5rem]">
                  {alert.action === 'OPEN' && (
                    <>
                      <span>{alert.contracts} ct @ ${Math.abs(alert.net_premium).toFixed(2)}</span>
                      {alert.strike != null && (
                        <span>${alert.strike.toFixed(0)}{alert.option_type?.[0] || ''}</span>
                      )}
                      {alert.expiration_date && (
                        <span>exp {alert.expiration_date}</span>
                      )}
                      {alert.max_loss != null && (
                        <span className="text-loss">risk ${alert.max_loss.toFixed(0)}</span>
                      )}
                      {alert.max_profit != null && (
                        <span className="text-profit">
                          target ${alert.max_profit > 99999 ? '∞' : alert.max_profit.toFixed(0)}
                        </span>
                      )}
                      {alert.confidence != null && (
                        <span className={alert.confidence > 0.7 ? 'text-profit' : 'text-caution'}>
                          {(alert.confidence * 100).toFixed(0)}% conf
                        </span>
                      )}
                      <span className="text-muted">{alert.regime.replace(/_/g, ' ')}</span>
                    </>
                  )}
                  {alert.action === 'CLOSE' && (
                    <>
                      <span>{alert.contracts} ct @ ${Math.abs(alert.net_premium).toFixed(2)}</span>
                      {alert.exit_reason && (
                        <span className="text-muted">{alert.exit_reason}</span>
                      )}
                      {alert.underlying_entry != null && alert.underlying_exit != null && (
                        <span>SPY ${alert.underlying_entry.toFixed(2)}→${alert.underlying_exit.toFixed(2)}</span>
                      )}
                      {alert.commission != null && alert.commission > 0 && (
                        <span className="text-muted">comm ${alert.commission.toFixed(2)}</span>
                      )}
                      {alert.strike != null && alert.expiration_date && (
                        <span>${alert.strike.toFixed(0)}{alert.option_type?.[0] || ''} {alert.expiration_date.slice(5)}</span>
                      )}
                    </>
                  )}
                </div>

                {/* Line 3: Display string (legs breakdown) */}
                {alert.display && (
                  <div className="mt-0.5 text-xxs font-mono text-terminal-500 pl-[4.5rem]">
                    {alert.display}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
