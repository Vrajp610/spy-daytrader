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
      className="px-3 py-2 text-left cursor-pointer hover:text-white"
      onClick={() => handleSort(field)}
    >
      {label} {sortKey === field ? (sortDesc ? '\u2193' : '\u2191') : ''}
    </th>
  );

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Trade History ({total})</h2>
        <select
          value={filterStrategy}
          onChange={e => setFilterStrategy(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
        >
          <option value="">All Strategies</option>
          {strategies.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="overflow-x-auto max-h-80 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="text-gray-400 border-b border-gray-800 sticky top-0 bg-gray-900">
            <tr>
              <SortHeader label="Time" field="entry_time" />
              <SortHeader label="Strategy" field="strategy" />
              <SortHeader label="Dir" field="direction" />
              <th className="px-3 py-2 text-left">Entry</th>
              <th className="px-3 py-2 text-left">Exit</th>
              <th className="px-3 py-2 text-left">Qty</th>
              <SortHeader label="P&L" field="pnl" />
              <th className="px-3 py-2 text-left">Reason</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-4 text-center text-gray-500">No trades yet</td></tr>
            ) : sorted.map((t, i) => (
              <>
                <tr
                  key={`row-${i}`}
                  className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer"
                  onClick={() => setExpandedId(expandedId === (t.id ?? i) ? null : (t.id ?? i))}
                >
                  <td className="px-3 py-2 font-mono text-xs">
                    {new Date(t.entry_time).toLocaleString()}
                  </td>
                  <td className="px-3 py-2">
                    {t.strategy}
                    {t.confidence != null && (
                      <span
                        className={`inline-block w-2 h-2 rounded-full ml-2 ${
                          t.confidence > 0.7 ? 'bg-green-400' : t.confidence >= 0.5 ? 'bg-yellow-400' : 'bg-red-400'
                        }`}
                        title={`Confidence: ${(t.confidence * 100).toFixed(0)}%`}
                      />
                    )}
                  </td>
                  <td className={`px-3 py-2 font-medium ${t.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                    {t.direction}
                  </td>
                  <td className="px-3 py-2 font-mono">${t.entry_price.toFixed(2)}</td>
                  <td className="px-3 py-2 font-mono">${t.exit_price.toFixed(2)}</td>
                  <td className="px-3 py-2">{t.quantity}</td>
                  <td className={`px-3 py-2 font-mono font-medium ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ${t.pnl.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-400">
                    {t.exit_reason}
                    {t.is_partial && <span className="ml-1 text-yellow-400">(partial)</span>}
                  </td>
                </tr>
                {expandedId === (t.id ?? i) && (
                  <tr key={`detail-${i}`} className="bg-gray-800/50">
                    <td colSpan={8} className="px-4 py-3">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        {t.mae != null && (
                          <div>
                            <span className="text-gray-400">MAE:</span>{' '}
                            <span className="text-red-400 font-mono">${t.mae.toFixed(2)}</span>
                            {t.mae_pct != null && (
                              <span className="text-gray-500 ml-1">({(t.mae_pct * 100).toFixed(2)}%)</span>
                            )}
                          </div>
                        )}
                        {t.mfe != null && (
                          <div>
                            <span className="text-gray-400">MFE:</span>{' '}
                            <span className="text-green-400 font-mono">${t.mfe.toFixed(2)}</span>
                            {t.mfe_pct != null && (
                              <span className="text-gray-500 ml-1">({(t.mfe_pct * 100).toFixed(2)}%)</span>
                            )}
                          </div>
                        )}
                        {t.confidence != null && (
                          <div>
                            <span className="text-gray-400">Confidence:</span>{' '}
                            <span className={`font-mono font-medium ${
                              t.confidence > 0.7 ? 'text-green-400' : t.confidence >= 0.5 ? 'text-yellow-400' : 'text-red-400'
                            }`}>
                              {(t.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                        )}
                        {t.slippage != null && (
                          <div>
                            <span className="text-gray-400">Slippage:</span>{' '}
                            <span className="font-mono">${t.slippage.toFixed(4)}</span>
                          </div>
                        )}
                        {t.bars_held != null && (
                          <div>
                            <span className="text-gray-400">Bars Held:</span>{' '}
                            <span className="font-mono">{t.bars_held}</span>
                          </div>
                        )}
                        {t.commission != null && (
                          <div>
                            <span className="text-gray-400">Commission:</span>{' '}
                            <span className="font-mono">${t.commission.toFixed(2)}</span>
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
