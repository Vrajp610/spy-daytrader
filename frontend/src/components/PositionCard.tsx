import { useState, useEffect } from 'react';
import { getPosition } from '../services/api';
import type { Position } from '../types';

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
          <div className="flex justify-between items-center grid-line pb-2">
            <span className="label">Direction</span>
            <span className={`badge ${position.direction === 'LONG' ? 'badge-active' : 'badge-live'}`}>
              {position.direction === 'LONG' ? '\u25B2' : '\u25BC'} {position.direction}
            </span>
          </div>
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
            <span className="data-value text-sm text-loss">
              ${position.stop_loss.toFixed(2)}
              {position.effective_stop !== undefined && position.effective_stop !== position.stop_loss && (
                <span className="text-caution ml-1">
                  (eff: ${position.effective_stop.toFixed(2)})
                </span>
              )}
            </span>
          </div>
          <div className="flex justify-between grid-line pb-2">
            <span className="label">Target</span>
            <span className="data-value text-sm text-profit">${position.take_profit.toFixed(2)}</span>
          </div>
          <div className="flex justify-between grid-line pb-2">
            <span className="label">Strategy</span>
            <span className="text-sm text-terminal-200">{position.strategy}</span>
          </div>
          {position.scales_completed && position.scales_completed.length > 0 && (
            <div className="flex justify-between grid-line pb-2">
              <span className="label">Scales</span>
              <div className="flex gap-1">
                {[1, 2].map(s => (
                  <span
                    key={s}
                    className={`text-xxs px-1.5 py-0.5 rounded-full font-mono ${
                      position.scales_completed!.includes(s)
                        ? 'bg-profit/15 text-profit border border-profit/30'
                        : 'bg-terminal-700/50 text-subtle border border-terminal-600/30'
                    }`}
                  >
                    S{s}
                  </span>
                ))}
              </div>
            </div>
          )}
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
          <div className="flex justify-between">
            <span className="label">Entry Time</span>
            <span className="text-xxs font-mono tabular-nums text-muted">{new Date(position.entry_time).toLocaleTimeString()}</span>
          </div>
        </div>
      )}
    </div>
  );
}
