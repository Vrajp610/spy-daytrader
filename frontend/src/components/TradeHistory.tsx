import { useState } from 'react';
import type { Trade } from '../types';

interface Props {
  trades: Trade[];
  total: number;
}

type SortKey = 'entry_time' | 'pnl' | 'strategy' | 'direction';

export default function TradeHistory({ trades, total }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('entry_time');
  const [sortDesc, setSortDesc] = useState(true);
  const [filterStrategy, setFilterStrategy] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDesc(!sortDesc);
    else { setSortKey(key); setSortDesc(true); }
  };

  const filtered = trades.filter(t => !filterStrategy || t.strategy === filterStrategy);
  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0;
    if (sortKey === 'pnl') cmp = a.pnl - b.pnl;
    else if (sortKey === 'strategy') cmp = a.strategy.localeCompare(b.strategy);
    else if (sortKey === 'direction') cmp = a.direction.localeCompare(b.direction);
    else cmp = a.entry_time.localeCompare(b.entry_time);
    return sortDesc ? -cmp : cmp;
  });

  const strategies = [...new Set(trades.map(t => t.strategy))];

  const SortHeader = ({ label, field }: { label: string; field: SortKey }) => (
    <th
      className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest cursor-pointer hover:text-terminal-200 transition-colors"
      onClick={() => handleSort(field)}
    >
      {label}{' '}
      {sortKey === field && (
        <span className="text-accent">{sortDesc ? '\u25BC' : '\u25B2'}</span>
      )}
    </th>
  );

  return (
    <div className="card p-4">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <h2 className="card-title">Trade History</h2>
          <span className="badge badge-paper">{total}</span>
        </div>
        <select
          value={filterStrategy}
          onChange={e => setFilterStrategy(e.target.value)}
          className="select w-auto text-xs"
        >
          <option value="">All Strategies</option>
          {strategies.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="overflow-x-auto max-h-80 overflow-y-auto">
        <table className="data-table">
          <thead className="sticky top-0 bg-terminal-800/95 backdrop-blur-sm">
            <tr>
              <SortHeader label="Time" field="entry_time" />
              <SortHeader label="Strategy" field="strategy" />
              <SortHeader label="Dir" field="direction" />
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest">Entry</th>
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest">Exit</th>
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest">Qty</th>
              <SortHeader label="P&L" field="pnl" />
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest">Reason</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-muted">No trades yet</td></tr>
            ) : sorted.map((t, i) => (
              <>
                <tr
                  key={`row-${i}`}
                  className="cursor-pointer"
                  onClick={() => setExpandedId(expandedId === (t.id ?? i) ? null : (t.id ?? i))}
                >
                  <td className="font-mono text-xxs tabular-nums text-muted">
                    {new Date(t.entry_time).toLocaleString()}
                  </td>
                  <td className="text-terminal-200">
                    {t.strategy}
                    {t.confidence != null && (
                      <span
                        className={`inline-block w-1.5 h-1.5 rounded-full ml-1.5 ${
                          t.confidence > 0.7 ? 'bg-profit' : t.confidence >= 0.5 ? 'bg-caution' : 'bg-loss'
                        }`}
                        title={`Confidence: ${(t.confidence * 100).toFixed(0)}%`}
                      />
                    )}
                  </td>
                  <td className={`font-medium ${t.direction === 'LONG' ? 'text-profit' : 'text-loss'}`}>
                    {t.direction}
                  </td>
                  <td className="font-mono tabular-nums">${t.entry_price.toFixed(2)}</td>
                  <td className="font-mono tabular-nums">${t.exit_price.toFixed(2)}</td>
                  <td className="tabular-nums">{t.quantity}</td>
                  <td className={`font-mono tabular-nums font-medium ${t.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                    ${t.pnl.toFixed(2)}
                  </td>
                  <td className="text-xxs text-muted">
                    {t.exit_reason}
                    {t.is_partial && <span className="ml-1 text-caution">(partial)</span>}
                  </td>
                </tr>
                {expandedId === (t.id ?? i) && (
                  <tr key={`detail-${i}`} className="animate-fade-in !bg-terminal-700/20">
                    <td colSpan={8} className="px-4 py-3">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        {t.mae != null && (
                          <div>
                            <span className="label">MAE</span>{' '}
                            <span className="text-loss font-mono">${t.mae.toFixed(2)}</span>
                            {t.mae_pct != null && (
                              <span className="text-subtle ml-1 text-xxs">({(t.mae_pct * 100).toFixed(2)}%)</span>
                            )}
                          </div>
                        )}
                        {t.mfe != null && (
                          <div>
                            <span className="label">MFE</span>{' '}
                            <span className="text-profit font-mono">${t.mfe.toFixed(2)}</span>
                            {t.mfe_pct != null && (
                              <span className="text-subtle ml-1 text-xxs">({(t.mfe_pct * 100).toFixed(2)}%)</span>
                            )}
                          </div>
                        )}
                        {t.confidence != null && (
                          <div>
                            <span className="label">Confidence</span>{' '}
                            <span className={`font-mono font-medium ${
                              t.confidence > 0.7 ? 'text-profit' : t.confidence >= 0.5 ? 'text-caution' : 'text-loss'
                            }`}>
                              {(t.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                        )}
                        {t.slippage != null && (
                          <div>
                            <span className="label">Slippage</span>{' '}
                            <span className="font-mono text-terminal-200">${t.slippage.toFixed(4)}</span>
                          </div>
                        )}
                        {t.bars_held != null && (
                          <div>
                            <span className="label">Bars Held</span>{' '}
                            <span className="font-mono text-terminal-200">{t.bars_held}</span>
                          </div>
                        )}
                        {t.commission != null && (
                          <div>
                            <span className="label">Commission</span>{' '}
                            <span className="font-mono text-terminal-200">${t.commission.toFixed(2)}</span>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
