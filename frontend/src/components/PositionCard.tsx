import { useState, useEffect } from 'react';
import { getPosition } from '../services/api';
import type { Position, OptionLeg } from '../types';

export default function PositionCard() {
  const [position, setPosition] = useState<Position | null>(null);

  useEffect(() => {
    const refresh = async () => {
      try {
        const data = await getPosition();
        setPosition(data.position);
      } catch { /* ignore */ }
    };
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, []);

  const directionAccent = position?.direction === 'LONG' ? 'accent-left-green' : position?.direction === 'SHORT' ? 'accent-left-red' : '';
  const isOptions = !!position?.option_strategy_type;

  return (
    <div className={`card p-4 ${directionAccent}`}>
      <h2 className="card-title mb-3">Current Position</h2>

      {!position ? (
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <p className="text-muted text-sm">No open position</p>
          <p className="text-subtle text-xxs mt-1">Waiting for signal...</p>
        </div>
      ) : (
        <div className="space-y-2">
          {isOptions && (
            <div className="flex justify-between items-center grid-line pb-2">
              <span className="label">Type</span>
              <span className="badge badge-paper font-mono text-xs">
                {position.option_strategy_abbrev || position.option_strategy_type}
              </span>
            </div>
          )}
          <div className="flex justify-between items-center grid-line pb-2">
            <span className="label">Direction</span>
            <span className={`badge ${position.direction === 'LONG' ? 'badge-active' : 'badge-live'}`}>
              {position.direction === 'LONG' ? '\u25B2' : '\u25BC'} {position.direction}
            </span>
          </div>

          {isOptions ? (
            <>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Contracts</span>
                <span className="data-value text-sm">{position.contracts}</span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Premium</span>
                <span className="data-value text-sm font-mono">
                  ${Math.abs(position.net_premium ?? 0).toFixed(2)}
                  <span className="text-subtle text-xxs ml-1">
                    {(position.net_premium ?? 0) < 0 ? 'credit' : 'debit'}
                  </span>
                </span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Max Loss</span>
                <span className="data-value text-sm text-loss font-mono">${(position.max_loss ?? 0).toFixed(2)}</span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Max Profit</span>
                <span className="data-value text-sm text-profit font-mono">
                  {(position.max_profit ?? 0) > 99999 ? 'Unlimited' : `$${(position.max_profit ?? 0).toFixed(2)}`}
                </span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Expiration</span>
                <span className="data-value text-sm font-mono">{position.expiration_date}</span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Greeks</span>
                <span className="text-xxs font-mono text-terminal-200">
                  <span className="text-muted mr-1">&delta;</span>{(position.net_delta ?? 0).toFixed(3)}
                  <span className="text-muted ml-2 mr-1">&theta;</span>{(position.net_theta ?? 0).toFixed(3)}
                </span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Underlying</span>
                <span className="data-value text-sm font-mono">${(position.underlying_price ?? 0).toFixed(2)}</span>
              </div>
            </>
          ) : (
            <>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Qty</span>
                <span className="data-value text-sm">
                  {position.original_quantity && position.original_quantity !== position.quantity
                    ? `${position.quantity} / ${position.original_quantity}`
                    : position.quantity}
                </span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Entry</span>
                <span className="data-value text-sm">${position.entry_price.toFixed(2)}</span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Stop</span>
                <span className="data-value text-sm text-loss">${position.stop_loss.toFixed(2)}</span>
              </div>
              <div className="flex justify-between grid-line pb-2">
                <span className="label">Target</span>
                <span className="data-value text-sm text-profit">${position.take_profit.toFixed(2)}</span>
              </div>
            </>
          )}

          <div className="flex justify-between grid-line pb-2">
            <span className="label">Strategy</span>
            <span className="text-sm text-terminal-200">{position.strategy}</span>
          </div>

          {position.unrealized_pnl !== undefined && (
            <div className="flex justify-between grid-line pb-2">
              <span className="label">Unrealized P&L</span>
              <span className={`font-mono font-semibold text-sm ${
                position.unrealized_pnl >= 0
                  ? 'text-profit text-glow-green'
                  : 'text-loss text-glow-red'
              }`}>
                ${position.unrealized_pnl.toFixed(2)}
              </span>
            </div>
          )}

          {/* Options legs detail */}
          {isOptions && position.legs && position.legs.length > 0 && (
            <div className="mt-2 pt-2 border-t border-terminal-600/30">
              <span className="label text-xxs mb-1 block">Legs</span>
              <div className="space-y-1">
                {position.legs.map((leg: OptionLeg, i: number) => (
                  <div key={i} className="flex justify-between text-xxs font-mono">
                    <span className={leg.action.includes('SELL') ? 'text-loss' : 'text-profit'}>
                      {leg.action.includes('SELL') ? 'SELL' : 'BUY'} ${leg.strike}{leg.option_type[0]}
                    </span>
                    <span className="text-muted">${leg.premium.toFixed(2)}</span>
                    <span className="text-subtle">&delta;{Math.abs(leg.delta).toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex justify-between">
            <span className="label">Entry Time</span>
            <span className="text-xxs font-mono tabular-nums text-muted">{new Date(position.entry_time).toLocaleTimeString()}</span>
          </div>
        </div>
      )}
    </div>
  );
}
