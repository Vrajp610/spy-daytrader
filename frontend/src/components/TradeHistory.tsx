import { useState } from 'react';
import type { Trade } from '../types';

interface Props {
  trades: Trade[];
  total: number;
}

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

  const isOptions = (t: Trade) => !!t.option_strategy_type;

  return (
    <div className="card p-4">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <h2 className="card-title">Trade History</h2>
          <span className="badge badge-paper" title={`${total} total trades`}>
            {filtered.length}{total > filtered.length ? ` / ${total}` : ''}
          </span>
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
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest hidden sm:table-cell">Trade</th>
              <SortHeader label="Strategy" field="strategy" />
              <SortHeader label="Dir" field="direction" />
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest hidden md:table-cell">Premium</th>
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest hidden md:table-cell">Cost</th>
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest hidden sm:table-cell">Ct</th>
              <SortHeader label="P&L" field="pnl" />
              <th className="px-2.5 py-2 text-left text-xxs font-medium uppercase tracking-widest hidden lg:table-cell">Reason</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr><td colSpan={9} className="px-3 py-6 text-center text-muted">No trades yet — tap a row to expand details</td></tr>
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
                  <td className="font-mono text-xxs hidden sm:table-cell">
                    {isOptions(t) ? (
                      <div>
                        <span className="badge badge-paper text-xxs px-1.5">
                          {TYPE_ABBREV[t.option_strategy_type!] || t.option_strategy_type}
                        </span>
                        {t.strike != null && t.expiration_date && (
                          <div className="text-terminal-300 mt-0.5">
                            ${t.strike.toFixed(0)}{t.option_type?.[0] || ''}{' '}
                            {t.expiration_date.slice(5)}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="text-muted">EQ</span>
                    )}
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
                  <td className="font-mono tabular-nums hidden md:table-cell">
                    {isOptions(t) ? (
                      <>
                        <span>${Math.abs(t.net_premium ?? t.entry_price).toFixed(2)}</span>
                        <span className="text-subtle text-xxs ml-0.5">
                          {(t.net_premium ?? 0) < 0 ? 'cr' : 'db'}
                        </span>
                      </>
                    ) : (
                      <>${t.entry_price.toFixed(2)}</>
                    )}
                  </td>
                  <td className="font-mono tabular-nums text-terminal-200 hidden md:table-cell">
                    {isOptions(t) ? (
                      `$${(Math.abs(t.net_premium ?? t.entry_price) * (t.contracts ?? t.quantity) * 100).toFixed(0)}`
                    ) : (
                      `$${(t.entry_price * t.quantity).toFixed(0)}`
                    )}
                  </td>
                  <td className="tabular-nums hidden sm:table-cell">{t.contracts ?? t.quantity}</td>
                  <td className={`font-mono tabular-nums font-medium ${t.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                    ${t.pnl.toFixed(2)}
                  </td>
                  <td className="text-xxs text-muted hidden lg:table-cell">
                    {t.exit_reason}
                    {t.is_partial && <span className="ml-1 text-caution">(partial)</span>}
                  </td>
                </tr>
                {expandedId === (t.id ?? i) && (
                  <tr key={`detail-${i}`} className="animate-fade-in !bg-terminal-700/20">
                    <td colSpan={9} className="px-4 py-3">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        {isOptions(t) && (
                          <>
                            <div>
                              <span className="label">Total Cost</span>{' '}
                              <span className="font-mono text-terminal-200">
                                ${(Math.abs(t.net_premium ?? 0) * (t.contracts ?? t.quantity) * 100).toFixed(2)}
                              </span>
                            </div>
                            <div>
                              <span className="label">Max Loss</span>{' '}
                              <span className="text-loss font-mono">${(t.max_loss ?? 0).toFixed(2)}</span>
                            </div>
                            <div>
                              <span className="label">Max Profit</span>{' '}
                              <span className="text-profit font-mono">
                                {(t.max_profit ?? 0) > 99999 ? 'Unlimited' : `$${(t.max_profit ?? 0).toFixed(2)}`}
                              </span>
                            </div>
                            <div>
                              <span className="label">Risk/Reward</span>{' '}
                              <span className="font-mono text-terminal-200">
                                {(t.max_loss ?? 0) > 0 && (t.max_profit ?? 0) < 99999
                                  ? `1:${((t.max_profit ?? 0) / (t.max_loss ?? 1)).toFixed(2)}`
                                  : '--'}
                              </span>
                            </div>
                            {t.entry_delta != null && (
                              <div>
                                <span className="label">Entry Delta</span>{' '}
                                <span className="font-mono text-terminal-200">{t.entry_delta.toFixed(3)}</span>
                              </div>
                            )}
                            {t.entry_theta != null && (
                              <div>
                                <span className="label">Entry Theta</span>{' '}
                                <span className="font-mono text-terminal-200">{t.entry_theta.toFixed(3)}</span>
                              </div>
                            )}
                            {t.entry_iv != null && (
                              <div>
                                <span className="label">Entry IV</span>{' '}
                                <span className="font-mono text-terminal-200">{(t.entry_iv * 100).toFixed(1)}%</span>
                              </div>
                            )}
                            {t.regime && (
                              <div>
                                <span className="label">Regime</span>{' '}
                                <span className="font-mono text-terminal-200">{t.regime.replace(/_/g, ' ')}</span>
                              </div>
                            )}
                            {t.underlying_entry != null && (
                              <div>
                                <span className="label">SPY Entry</span>{' '}
                                <span className="font-mono text-terminal-200">${t.underlying_entry.toFixed(2)}</span>
                              </div>
                            )}
                            {t.underlying_exit != null && (
                              <div>
                                <span className="label">SPY Exit</span>{' '}
                                <span className="font-mono text-terminal-200">${t.underlying_exit.toFixed(2)}</span>
                              </div>
                            )}
                            {t.strike != null && (
                              <div>
                                <span className="label">Strike</span>{' '}
                                <span className="font-mono text-terminal-200">${t.strike.toFixed(0)} {t.option_type || ''}</span>
                              </div>
                            )}
                            {t.expiration_date && (
                              <div>
                                <span className="label">Expiration</span>{' '}
                                <span className="font-mono text-terminal-200">{t.expiration_date}</span>
                              </div>
                            )}
                          </>
                        )}
                        {t.mae != null && (
                          <div>
                            <span className="label">MAE</span>{' '}
                            <span className="text-loss font-mono">${t.mae.toFixed(2)}</span>
                          </div>
                        )}
                        {t.mfe != null && (
                          <div>
                            <span className="label">MFE</span>{' '}
                            <span className="text-profit font-mono">${t.mfe.toFixed(2)}</span>
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
                        {t.commission != null && (
                          <div>
                            <span className="label">Commission</span>{' '}
                            <span className="font-mono text-terminal-200">${t.commission.toFixed(2)}</span>
                          </div>
                        )}
                        {t.slippage != null && t.slippage > 0 && (
                          <div>
                            <span className="label">Spread Cost</span>{' '}
                            <span className="font-mono text-terminal-200">${t.slippage.toFixed(4)}</span>
                          </div>
                        )}
                        {t.pnl_pct != null && (
                          <div>
                            <span className="label">P&L %</span>{' '}
                            <span className={`font-mono font-medium ${t.pnl_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                              {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(1)}%
                            </span>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
          {sorted.length > 0 && (() => {
            const totalPnl = sorted.reduce((s, t) => s + t.pnl, 0);
            const wins = sorted.filter(t => t.pnl > 0).length;
            return (
              <tfoot className="border-t border-terminal-600/40 bg-terminal-800/60">
                <tr>
                  <td colSpan={7} className="px-2.5 py-1.5 text-xxs text-muted">
                    {sorted.length} trades · {wins}W / {sorted.length - wins}L · WR {sorted.length > 0 ? ((wins / sorted.length) * 100).toFixed(0) : 0}%
                  </td>
                  <td className={`px-2.5 py-1.5 font-mono font-semibold text-xs ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
                  </td>
                  <td />
                </tr>
              </tfoot>
            );
          })()}
        </table>
      </div>
    </div>
  );
}
